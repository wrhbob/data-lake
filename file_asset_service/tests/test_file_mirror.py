from datetime import date
from types import SimpleNamespace

from app.file_mirror import FileMirror, build_archive_mirror_relative_path, sanitize_path_component


def cell(value):
    return {"value": value, "source_level": "test", "tagged_by": "test", "tagged_at": "2026-06-30T10:00:00+08:00"}


def test_sanitize_path_component_removes_unsafe_path_characters():
    assert sanitize_path_component('../《泉州:信息价》2026/01?.pdf') == '_《泉州_信息价》2026_01_.pdf'
    assert sanitize_path_component('  ') == '_'


def test_cost_info_mirror_path_uses_domain_region_year_and_original_file_name():
    archive = SimpleNamespace(
        domain_type="cost_info",
        region_code="350500",
        publish_date=date(2026, 1, 12),
        title="泉州信息价",
        metadata_payload={
            "province_raw": cell("福建省"),
            "city_raw": cell("泉州市"),
            "period": cell("2026-01"),
        },
    )
    mounted = SimpleNamespace(file_role="main_document", display_name=None)
    asset = SimpleNamespace(file_id="file-001", file_name="《泉州工程造价管理》2026年第1期.pdf")

    relative_path = build_archive_mirror_relative_path(archive, mounted, asset)

    assert relative_path == "信息价/福建省/泉州市/2026/《泉州工程造价管理》2026年第1期.pdf"


def test_attachment_mirror_path_uses_archive_folder_to_avoid_name_collisions():
    archive = SimpleNamespace(
        domain_type="trading",
        region_code="330100",
        publish_date=date(2026, 6, 8),
        title="学校施工招标公告",
        metadata_payload={"province_raw": cell("浙江省"), "city_raw": cell("杭州市")},
    )
    mounted = SimpleNamespace(file_role="attachment", display_name="控制价.zip")
    asset = SimpleNamespace(file_id="file-002", file_name="控制价.zip")

    relative_path = build_archive_mirror_relative_path(archive, mounted, asset)

    assert relative_path == "招投标/浙江省/杭州市/2026/学校施工招标公告/attachment-控制价.zip"


def test_export_file_retries_after_smb_file_exists_replace_error(tmp_path, monkeypatch):
    archive = SimpleNamespace(
        domain_type="cost_info",
        region_code="350500",
        publish_date=date(2026, 1, 12),
        title="泉州信息价",
        metadata_payload={"province_raw": cell("福建省"), "city_raw": cell("泉州市"), "period": cell("2026-01")},
    )
    mounted = SimpleNamespace(file_role="main_document", display_name=None)
    asset = SimpleNamespace(file_id="file-001", file_name="泉州.pdf", file_size=3)
    mirror = FileMirror(root=tmp_path)
    target = tmp_path / build_archive_mirror_relative_path(archive, mounted, asset)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    real_replace = __import__("os").replace
    calls = {"count": 0}

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileExistsError(17, "File exists", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr("app.file_mirror.os.replace", flaky_replace)

    result = mirror.export_file(archive, mounted, asset, b"new")

    assert result["mirror_status"] == "mirrored"
    assert target.read_bytes() == b"new"
    assert calls["count"] == 2
