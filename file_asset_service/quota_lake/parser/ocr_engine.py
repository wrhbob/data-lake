"""PaddleOCR PP-StructureV3 封装 (从 parse_dinge.py §3 零改动提取).

惰性导入 PaddleOCR, 无 GPU 时默认 CPU。
"""


class OcrEngine:
    """OCR 引擎 — 封装 PP-StructureV3 版面 + 表格识别.

    用法::

        ocr = OcrEngine()
        blocks = ocr.parse_page("page_0030.png")
        # blocks = [{type: "text"|"table"|"title", bbox: [...], text|html: ...}, ...]
    """

    def __init__(self, lang: str = "ch", use_gpu: bool = False):
        self._lang = lang
        self._use_gpu = use_gpu
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from paddleocr import PPStructureV3

            device = "gpu" if self._use_gpu else "cpu"
            self._engine = PPStructureV3(device=device, lang=self._lang)
        return self._engine

    def parse_page(self, img_path: str) -> list[dict]:
        """解析单个页面图像, 返回 block 列表.

        Returns:
            每个 block 为 dict: {type, bbox, text|html, ...}
        """
        result = self.engine.predict(img_path)
        blocks = []
        for res in result:
            for blk in res.get("parsing_res_list", res.get("layout_parsing_result", [])):
                blocks.append(blk)
        return blocks
