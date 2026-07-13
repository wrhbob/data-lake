from app.adapters.base_cost_info_adapter import AdapterResult, BaseCostInfoAdapter, DiscoveredIssue
from app.adapters.beijing_pdf import BeijingPdfAdapter
from app.adapters.chongqing_pdf import ChongqingPdfAdapter
from app.adapters.hubei_pdf import HubeiPdfAdapter
from app.adapters.hefei_pdf import HefeiPdfAdapter
from app.adapters.jinan_pdf import JinanPdfAdapter
from app.adapters.jiuquan_pdf import JiuquanPdfAdapter
from app.adapters.linxia_xlsx import LinxiaXlsxAdapter
from app.adapters.mock_adapter import MockCostInfoAdapter
from app.adapters.nanjing_pdf import NanjingPdfAdapter
from app.adapters.ningbo_pdf import NingboPdfAdapter
from app.adapters.pingliang_pdf import PingliangPdfAdapter
from app.adapters.qingyang_pdf import QingyangPdfAdapter
from app.adapters.shanghai_excel import ShanghaiExcelAdapter
from app.adapters.shangluo_pdf import ShangluoPdfAdapter
from app.adapters.sichuan_pdf import SichuanPdfAdapter
from app.adapters.static_list_detail_package import StaticListDetailPackageAdapter
from app.adapters.wuhan_pdf import WuhanPdfAdapter
from app.adapters.xinjiang_aspnet_ajax import XinjiangAspnetAjaxAreaAdapter
from app.adapters.zhangye_zip import ZhangyeZipAdapter

ADAPTERS: dict[str, type] = {
    "mock": MockCostInfoAdapter,
    "sichuan_pdf": SichuanPdfAdapter,
    "chongqing_pdf": ChongqingPdfAdapter,
    "aspnet_ajax_area": XinjiangAspnetAjaxAreaAdapter,
    "beijing_pdf": BeijingPdfAdapter,
    "shanghai_excel": ShanghaiExcelAdapter,
    "hubei_pdf": HubeiPdfAdapter,
    "wuhan_pdf": WuhanPdfAdapter,
    "nanjing_pdf": NanjingPdfAdapter,
    "ningbo_pdf": NingboPdfAdapter,
    "pingliang_pdf": PingliangPdfAdapter,
    "qingyang_pdf": QingyangPdfAdapter,
    "shangluo_pdf": ShangluoPdfAdapter,
    "hefei_pdf": HefeiPdfAdapter,
    "jinan_pdf": JinanPdfAdapter,
    "jiuquan_pdf": JiuquanPdfAdapter,
    "linxia_xlsx": LinxiaXlsxAdapter,
    "zhangye_zip": ZhangyeZipAdapter,
    "static_list_detail_package": StaticListDetailPackageAdapter,
}


def get_adapter(adapter_kind: str):
    if adapter_kind not in ADAPTERS:
        raise ValueError(f"Unknown adapter_kind={adapter_kind!r}. Known: {sorted(ADAPTERS)}")
    return ADAPTERS[adapter_kind]()


__all__ = ["ADAPTERS", "AdapterResult", "BaseCostInfoAdapter", "DiscoveredIssue", "get_adapter"]
