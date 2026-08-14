信息价提取 v3（per-city 独立架构）
目标：PDF → Excel，按 TOC 章节 1:1 对应 Sheet（P1/P2/P3），OCR 出啥输出啥不推断。
P1 = 市场信息价（Sheet 1）/ P2 = 建筑新型材料（Sheet 3）/ P3 = PC 构件（Sheet 2）

怎么跑（2026-07-31 改 per-city 独立）：
  cd {城市目录}        # 成都 / 重庆 / 北京
  ../.venv/Scripts/python.exe run.py <pdf> --city {城市} --period 06 --year 2026
  输出 → {城市}/4_输出/{city}_{year}年{period}期_市场信息价.xlsx
  缓存 → {城市}/3_中间产物/{code}_{period}_ocr/

目录（每城市独立一套，根目录只有共享部分）：
  {city}/
    ├── 1_脚本/      step1_mineru → step6_output
    ├── 2_输入/      放 PDF
    ├── 3_中间产物/  OCR 缓存
    ├── 4_输出/      Excel（Sheet 1/2/3）
    ├── 5_日志/      process.log
    └── run.py       城市入口
  共享（根目录）：
    6_配置/          城市模板 YAML + section_titles.json + skip_keywords.json
    .venv/           项目统一 venv（per-city 无 venv）

铁律：OCR 空就空，不推断不填充；区名错手工改 6_配置\district_correction.json
进度：成都 06/02 + 重庆 06 + 北京 06 全部 baseline 零回归
