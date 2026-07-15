from app.adapters import hubei_pdf, linxia_xlsx, pingliang_pdf, wuhan_pdf, xinjiang_aspnet_ajax
from app.xinjiang_cost_info import XinjiangIssueList


def test_hubei_history_pagination_reads_until_short_page(monkeypatch):
    def fake_list(_client, *, parser, page, page_size):
        pages = {
            1: [{"source_item_key": "a"}, {"source_item_key": "b"}],
            2: [{"source_item_key": "c"}],
        }
        return pages.get(page, [])

    monkeypatch.setattr(hubei_pdf, "list_hubei_cost_info_issues", fake_list)

    rows = hubei_pdf._list_all_pages(object(), parser={}, page_size=2, page_limit=20)

    assert [row["source_item_key"] for row in rows] == ["a", "b", "c"]


def test_wuhan_linxia_and_pingliang_stop_when_page_repeats(monkeypatch):
    def page_rows(_client, *, parser, page=None, page_no=None, **_kwargs):
        current_page = page if page is not None else page_no
        return [{"source_item_key": "first"}] if current_page in {1, 2} else []

    monkeypatch.setattr(wuhan_pdf, "list_wuhan_cost_info_issues", page_rows)
    monkeypatch.setattr(linxia_xlsx, "list_linxia_cost_info_issues", page_rows)
    monkeypatch.setattr(pingliang_pdf, "list_pingliang_cost_info_issues", page_rows)

    assert wuhan_pdf._list_all_pages(object(), parser={}, page_limit=20) == [{"source_item_key": "first"}]
    assert linxia_xlsx._list_all_pages(object(), parser={}, page_limit=20) == [{"source_item_key": "first"}]
    assert pingliang_pdf._list_all_pages(object(), parser={}, page_limit=20) == [{"source_item_key": "first"}]


def test_xinjiang_history_pagination_uses_reported_total(monkeypatch):
    def fake_list(_client, *, parser, page, page_size):
        assert page_size == 2
        pages = {
            1: XinjiangIssueList(rows=[{"id": "a"}, {"id": "b"}], total=3),
            2: XinjiangIssueList(rows=[{"id": "c"}], total=3),
        }
        return pages.get(page, XinjiangIssueList(rows=[], total=3))

    monkeypatch.setattr(xinjiang_aspnet_ajax, "list_xinjiang_area_issues", fake_list)
    monkeypatch.setattr(xinjiang_aspnet_ajax, "_row_source_item_key", lambda row: row["id"])

    rows = xinjiang_aspnet_ajax._list_all_pages(object(), parser={}, page_size=2, page_limit=20)

    assert [row["id"] for row in rows] == ["a", "b", "c"]
