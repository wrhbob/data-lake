from pathlib import Path
import subprocess
import textwrap


def test_trading_does_not_offer_exchange_center_filter():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "trading";
          state.viewMode = "archives";
          JSON.stringify({{
            filterKeys: domainConfigs.trading.filters.map((filter) => filter.key),
            visibleKeys: visibleFilterKeys(),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"filterKeys":["region_code","publish_year","publish_month","publish_day","notice_type_raw","channel_type"]' in completed.stdout
    assert '"visibleKeys":["region_code","publish_year","publish_month"]' in completed.stdout
    assert "exchange_name" not in completed.stdout


def test_trading_day_filter_only_lists_days_with_data_for_selected_month():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "trading";
          state.viewMode = "archives";
          state.filters.publish_year = "2026";
          state.filters.publish_month = "06";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ publish_date: "2026-06-22", metadata: {{ publish_date_raw: cell("2026年6月22日") }} }},
            {{ publish_date: "2026-06-23", metadata: {{ publish_date_raw: cell("2026年6月23日") }} }},
            {{ publish_date: "2026-05-30", metadata: {{ publish_date_raw: cell("2026年5月30日") }} }},
          ];
          const dayFilter = domainConfigs.trading.filters.find((filter) => filter.key === "publish_day");
          JSON.stringify({{
            visible: visibleFilterKeys(),
            dayOptions: filterOptions(dayFilter),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"visible":["region_code","publish_year","publish_month","publish_day"]' in completed.stdout
    assert '"dayOptions":[{"label":"全部","value":"all"},{"label":"22日","value":"22"},{"label":"23日","value":"23"}]' in completed.stdout


def test_trading_day_filter_lists_month_days_when_year_is_all():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "trading";
          state.viewMode = "archives";
          state.filters.publish_year = "all";
          state.filters.publish_month = "06";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ publish_date: "2025-06-21", metadata: {{ publish_date_raw: cell("2025年6月21日") }} }},
            {{ publish_date: "2026-06-22", metadata: {{ publish_date_raw: cell("2026年6月22日") }} }},
            {{ publish_date: "2026-05-23", metadata: {{ publish_date_raw: cell("2026年5月23日") }} }},
          ];
          const dayFilter = domainConfigs.trading.filters.find((filter) => filter.key === "publish_day");
          JSON.stringify({{
            visible: visibleFilterKeys(),
            dayOptions: filterOptions(dayFilter),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"visible":["region_code","publish_year","publish_month","publish_day"]' in completed.stdout
    assert '"dayOptions":[{"label":"全部","value":"all"},{"label":"21日","value":"21"},{"label":"22日","value":"22"}]' in completed.stdout


def test_trading_archives_sort_latest_publication_first_even_when_api_pages_arrive_out_of_order():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "trading";
          state.viewMode = "archives";
          state.archives = [
            {{ archive_id: "old", title: "7月6日", publish_date: "2026-07-06", created_at: "2026-07-15T10:00:00Z" }},
            {{ archive_id: "new", title: "7月15日", publish_date: "2026-07-15", created_at: "2026-07-15T09:00:00Z" }},
            {{ archive_id: "undated", title: "无发布时间", created_at: "2026-07-15T11:00:00Z" }},
          ];
          JSON.stringify(filteredArchives().map((item) => item.title));
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert completed.stdout.strip() == '["7月15日","7月6日","无发布时间"]'


def test_cost_info_beijing_year_and_month_filters_use_real_archives_only():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "11";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ region_code: "110000", metadata: {{ period: cell("2026-01") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-02") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-03") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-04") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-05") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-06") }} }},
            {{ region_code: "510500", metadata: {{ period: cell("2025-12") }} }},
          ];
          const yearFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_year");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          JSON.stringify({{
            yearOptions: filterOptions(yearFilter),
            monthOptions: filterOptions(monthFilter),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"yearOptions":[{"label":"全部","value":"all"},{"label":"2026","value":"2026"}]' in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"1月","value":"01"},{"label":"2月","value":"02"},{"label":"3月","value":"03"},{"label":"4月","value":"04"},{"label":"5月","value":"05"},{"label":"6月","value":"06"}]' in completed.stdout
    assert '"7月"' not in completed.stdout


def test_cost_info_selected_year_sorts_monthly_archives_from_january_to_december():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "11";
          state.filters.period_year = "2025";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "12月", region_code: "110000", metadata: {{ period: cell("2025-12") }} }},
            {{ title: "3月", region_code: "110000", metadata: {{ period: cell("2025-03") }} }},
            {{ title: "11月", region_code: "110000", metadata: {{ period: cell("2025-11") }} }},
            {{ title: "1月", region_code: "110000", metadata: {{ period: cell("2025-01") }} }},
            {{ title: "2月", region_code: "110000", metadata: {{ period: cell("2025-02") }} }},
          ];
          JSON.stringify(filteredArchives().map((item) => item.title));
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert completed.stdout.strip() == '["1月","2月","3月","11月","12月"]'


def test_cost_info_all_years_keeps_latest_first_api_order():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "11";
          state.filters.period_year = "all";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "2026年6月", region_code: "110000", metadata: {{ period: cell("2026-06") }} }},
            {{ title: "2025年12月", region_code: "110000", metadata: {{ period: cell("2025-12") }} }},
          ];
          JSON.stringify(filteredArchives().map((item) => item.title));
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert completed.stdout.strip() == '["2026年6月","2025年12月"]'


def test_cost_info_sichuan_city_and_luzhou_month_filters_use_real_archives_only():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "51";
          state.filters.region_code_city = "510500";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ region_code: "510500", publish_date: "2026-07-18", metadata: {{ city_raw: cell("泸州市"), period: cell("2026-05") }} }},
            {{ region_code: "510500", publish_date: "2026-08-18", metadata: {{ city_raw: cell("泸州市"), period: cell("2026-06") }} }},
            {{ region_code: "510600", publish_date: "2026-06-20", metadata: {{ city_raw: cell("德阳市"), period: cell("2026-04") }} }},
            {{ region_code: "510700", publish_date: "2026-07-01", metadata: {{ city_raw: cell("绵阳市"), period: cell("2026-06") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-06") }} }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          JSON.stringify({{
            cityOptions: cityOptions("51", regionFilter).map((option) => option.label),
            monthOptions: filterOptions(monthFilter),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"cityOptions":["全部","泸州市","德阳市","绵阳市"]' in completed.stdout
    assert '"阿坝藏族羌族自治州"' not in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"5月","value":"05"},{"label":"6月","value":"06"}]' in completed.stdout
    assert '"4月"' not in completed.stdout
    assert '"7月"' not in completed.stdout


def test_cost_info_single_beijing_city_option_stays_visible():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "11";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ region_code: "110000", metadata: {{ period_raw: cell("2026年06月北京工程造价信息"), period_start: cell("2026-06") }} }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          JSON.stringify({{ cityOptions: cityOptions("11", regionFilter).map((option) => option.label) }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"cityOptions":["全部","北京市"]' in completed.stdout


def test_cost_info_city_options_include_source_registry_cities_without_archives():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "35";
          state.archives = [];
          state.costInfoSources = [
            {{
              data_domain: "cost_info",
              source_type: "info_price",
              connector_type: "source_registry",
              province: "福建省",
              city: "三明市",
              region_code: "350400",
            }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          JSON.stringify({{ cityOptions: cityOptions("35", regionFilter).map((option) => option.label) }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"cityOptions":["全部","三明市"]' in completed.stdout


def test_cost_info_guizhou_issue_based_filters_project_issues_to_months_and_keep_issue_label():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "52";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [1, 2, 3, 4, 5].map((issue) => ({{
            title: "贵州省建设工程造价信息2026年第" + issue + "期",
            region_code: "520000",
            period_kind: "issue_based",
            metadata: {{
              period: cell("2026年第" + issue + "期"),
              period_raw: cell("2026年第" + issue + "期"),
              period_year: cell("2026"),
              period_issue_no: cell(issue),
              period_start: cell("2026-" + String(issue).padStart(2, "0")),
              period_month: cell(issue),
              publisher_type: cell("industry_association"),
              publisher_scope: cell("province"),
            }},
          }}));
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          const yearFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_year");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          const issueFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_issue");
          const defaultVisible = visibleFilterKeys();
          state.filterAdvancedOpen = true;
          const advancedVisible = visibleFilterKeys();
          const projectedMonthOptions = filterOptions(monthFilter);
          const marchTitles = state.archives.filter((item) => matchesMonth(item, monthFilter, "03")).map((item) => item.title);
          JSON.stringify({{
            defaultVisible,
            advancedVisible,
            cityOptions: cityOptions("52", regionFilter).map((option) => option.label),
            yearOptions: filterOptions(yearFilter),
            monthOptions: projectedMonthOptions,
            marchTitles,
            issueFilterExists: Boolean(issueFilter),
            issueTitle: renderTitleCell(state.archives[2]),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"defaultVisible":["region_code","period_year","period_month"]' in completed.stdout
    assert '"advancedVisible":["publisher_org",' in completed.stdout
    assert '"cityOptions":["全部"]' in completed.stdout
    assert '"yearOptions":[{"label":"全部","value":"all"},{"label":"2026","value":"2026"}]' in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"1月","value":"01"},{"label":"2月","value":"02"},{"label":"3月","value":"03"},{"label":"4月","value":"04"},{"label":"5月","value":"05"}]' in completed.stdout
    assert '"marchTitles":["贵州省建设工程造价信息2026年第3期"]' in completed.stdout
    assert '"issueFilterExists":false' in completed.stdout
    assert "第3期" in completed.stdout
    assert "issue-tag" in completed.stdout


def test_cost_info_beijing_monthly_filters_keep_month_without_issue_filter():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "11";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ region_code: "110000", period_kind: "monthly", metadata: {{ period: cell("2026-01") }} }},
            {{ region_code: "110000", period_kind: "monthly", metadata: {{ period: cell("2026-02") }} }},
            {{ region_code: "520000", period_kind: "issue_based", metadata: {{ period: cell("2026年第1期"), period_year: cell("2026"), period_issue_no: cell(1) }} }},
          ];
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          const issueFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_issue");
          JSON.stringify({{
            visible: visibleFilterKeys(),
            monthOptions: filterOptions(monthFilter),
            issueFilterExists: Boolean(issueFilter),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"visible":["region_code","period_year","period_month"]' in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"1月","value":"01"},{"label":"2月","value":"02"}]' in completed.stdout
    assert '"issueFilterExists":false' in completed.stdout


def test_cost_info_mixed_period_kinds_keep_month_filter_only_and_issue_as_row_label():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "all";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "北京2026年1月信息价", region_code: "110000", period_kind: "monthly", metadata: {{ period: cell("2026-01"), publisher_type: cell("official_housing_urban_rural_development"), publisher_scope: cell("province") }} }},
            {{ title: "泸州2026年6月信息价", region_code: "510500", period_kind: "monthly", metadata: {{ period: cell("2026-06"), publisher_type: cell("official_housing_urban_rural_development"), publisher_scope: cell("city") }} }},
            {{ title: "四川工程造价信息2026年第6期", region_code: "510000", period_kind: "monthly", metadata: {{ period_raw: cell("四川工程造价信息2026年第6期"), period_start: cell("2026-06"), publisher_type: cell("official_housing_urban_rural_development"), publisher_scope: cell("province") }} }},
            {{ title: "贵州2026年第1期", region_code: "520000", period_kind: "issue_based", metadata: {{ period: cell("2026年第1期"), period_year: cell("2026"), period_issue_no: cell(1), publisher_type: cell("industry_association"), publisher_scope: cell("province") }} }},
            {{ title: "贵州2026年第2期", region_code: "520000", period_kind: "issue_based", metadata: {{ period: cell("2026年第2期"), period_year: cell("2026"), period_issue_no: cell(2), publisher_type: cell("industry_association"), publisher_scope: cell("province") }} }},
          ];
          const publisherFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "publisher_org");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          const issueFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_issue");
          JSON.stringify({{
            visible: visibleFilterKeys(),
            publisherOptions: filterOptions(publisherFilter),
            monthOptions: filterOptions(monthFilter),
            issueFilterExists: Boolean(issueFilter),
            issueTitle: renderTitleCell(state.archives[4]),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"visible":["region_code","period_year","period_month"]' in completed.stdout
    assert '"publisherOptions":[{"label":"全部","value":"all"},{"label":"省站","value":"station:province"},{"label":"市站","value":"station:city"},{"label":"省协会","value":"association:province"}]' in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"1月","value":"01"},{"label":"6月","value":"06"}]' in completed.stdout
    assert '"issueFilterExists":false' in completed.stdout
    assert "第2期" in completed.stdout


def test_cost_info_publisher_org_options_and_filters_use_existing_type_scope_combo():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "北京省站", region_code: "110000", metadata: {{ period: cell("2026-01"), publisher_type: cell("official_housing_urban_rural_development"), publisher_scope: cell("province") }} }},
            {{ title: "武汉市站", region_code: "420100", metadata: {{ period: cell("2026-03"), publisher_type: cell("cost_station_public_institution"), publisher_scope: cell("city") }} }},
            {{ title: "贵州省协会", region_code: "520000", period_kind: "issue_based", metadata: {{ period: cell("2026年第1期"), period_year: cell("2026"), period_issue_no: cell(1), publisher_type: cell("industry_association"), publisher_scope: cell("province") }} }},
          ];
          const publisherFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "publisher_org");
          const options = filterOptions(publisherFilter);
          state.filters.publisher_org = "station:province";
          const provinceStation = filteredArchives().map((item) => item.title);
          state.filters.publisher_org = "station:city";
          const cityStation = filteredArchives().map((item) => item.title);
          state.filters.publisher_org = "association:province";
          const provinceAssociation = filteredArchives().map((item) => item.title);
          JSON.stringify({{
            filterKeys: domainConfigs.cost_info.filters.map((filter) => filter.key),
            options,
            provinceStation,
            cityStation,
            provinceAssociation,
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"filterKeys":["publisher_org","region_code","period_year","period_month","category_raw","channel_type"]' in completed.stdout
    assert '"options":[{"label":"全部","value":"all"},{"label":"省站","value":"station:province"},{"label":"市站","value":"station:city"},{"label":"省协会","value":"association:province"}]' in completed.stdout
    assert '"provinceStation":["北京省站"]' in completed.stdout
    assert '"cityStation":["武汉市站"]' in completed.stdout
    assert '"provinceAssociation":["贵州省协会"]' in completed.stdout


def test_cost_info_hidden_publisher_org_value_does_not_blank_cascading_options():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "51";
          state.filters.publisher_org = "station:province";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "泸州2026年5月信息价", region_code: "510500", metadata: {{ period: cell("2026-05"), publisher_scope: cell("city") }} }},
            {{ title: "绵阳2026年4月信息价", region_code: "510700", metadata: {{ period: cell("2026-04"), publisher_scope: cell("city") }} }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          const publisherFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "publisher_org");
          const yearFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_year");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          JSON.stringify({{
            activeVisible: activeVisibleFilters().map((filter) => filter.key),
            publisherValue: state.filters.publisher_org,
            publisherOptions: filterOptions(publisherFilter),
            cityOptions: cityOptions("51", regionFilter).map((option) => option.label),
            yearOptions: filterOptions(yearFilter),
            monthOptions: filterOptions(monthFilter),
            filteredTitles: filteredArchives().map((item) => item.title),
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"activeVisible":["region_code","period_year","period_month","category_raw","channel_type"]' in completed.stdout
    assert '"publisherValue":"station:province"' in completed.stdout
    assert '"publisherOptions":[{"label":"全部","value":"all"}]' in completed.stdout
    assert '"cityOptions":["全部","泸州市","绵阳市"]' in completed.stdout
    assert '"yearOptions":[{"label":"全部","value":"all"},{"label":"2026","value":"2026"}]' in completed.stdout
    assert '"monthOptions":[{"label":"全部","value":"all"},{"label":"4月","value":"04"},{"label":"5月","value":"05"}]' in completed.stdout
    assert '"filteredTitles":["泸州2026年5月信息价","绵阳2026年4月信息价"]' in completed.stdout


def test_cost_info_region_switch_resets_invalid_hidden_publisher_org_value():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.publisher_org = "station:province";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "北京2026年1月信息价", region_code: "110000", metadata: {{ period: cell("2026-01"), publisher_type: cell("official_housing_urban_rural_development"), publisher_scope: cell("province") }} }},
            {{ title: "泸州2026年5月信息价", region_code: "510500", metadata: {{ period: cell("2026-05"), publisher_scope: cell("city") }} }},
          ];
          renderAll = () => {{}};
          setActiveFilter({{ dataset: {{ filter: "region_code", value: "51" }} }});
          JSON.stringify({{
            publisherValue: state.filters.publisher_org,
            provinceValue: state.filters.region_code,
            cityValue: state.filters.region_code_city,
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"publisherValue":"all"' in completed.stdout
    assert '"provinceValue":"51"' in completed.stdout
    assert '"cityValue":"all"' in completed.stdout


def test_cost_info_hubei_mixed_period_kinds_month_filter_hides_issue_based_with_hint():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "42";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "湖北工程造价管理2026年第2期", region_code: "420000", period_kind: "issue_based", metadata: {{ period: cell("2026年第2期"), period_year: cell("2026"), period_issue_no: cell(2), publisher_type: cell("cost_station_public_institution"), publisher_scope: cell("province") }} }},
            {{ title: "武汉2026年3月综合价格信息", region_code: "420100", period_kind: "monthly", metadata: {{ period: cell("2026-03"), publisher_type: cell("cost_station_public_institution"), publisher_scope: cell("city") }} }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          const publisherFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "publisher_org");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          const mixedTitles = filteredArchives().map((item) => item.title);
          const cityLabelsAll = cityOptions("42", regionFilter).map((option) => option.label);
          const publisherOptionsAll = filterOptions(publisherFilter);
          const monthOptionsAll = filterOptions(monthFilter);
          const issueTag = renderTitleCell(state.archives[0]);
          state.filters.period_month = "03";
          const monthTitles = filteredArchives().map((item) => item.title);
          const hint = costInfoMonthIssueFilterHint();
          const filterHtml = renderCostInfoMonthIssueHint();
          state.filters.region_code_city = "420100";
          state.filters.period_month = "all";
          const wuhanOnlyTitles = filteredArchives().map((item) => item.title);
          JSON.stringify({{
            mixedTitles,
            cityLabelsAll,
            publisherOptionsAll,
            monthOptionsAll,
            monthTitles,
            hint,
            filterHtml,
            wuhanOnlyTitles,
            issueTag,
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"mixedTitles":["湖北工程造价管理2026年第2期","武汉2026年3月综合价格信息"]' in completed.stdout
    assert '"cityLabelsAll":["全部","武汉市"]' in completed.stdout
    assert '"publisherOptionsAll":[{"label":"全部","value":"all"},{"label":"省站","value":"station:province"},{"label":"市站","value":"station:city"}]' in completed.stdout
    assert '"monthOptionsAll":[{"label":"全部","value":"all"},{"label":"3月","value":"03"}]' in completed.stdout
    assert '"monthTitles":["武汉2026年3月综合价格信息"]' in completed.stdout
    assert "期号制源请清除月份按年查看" in completed.stdout
    assert '"wuhanOnlyTitles":["武汉2026年3月综合价格信息"]' in completed.stdout
    assert "第2期" in completed.stdout


def test_cost_info_cascade_filter_click_renders_locally_without_reloading_archives():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          let renderFiltersCount = 0;
          let renderAllCount = 0;
          let loadCurrentViewCount = 0;
          renderFilters = () => {{ renderFiltersCount += 1; }};
          renderAll = () => {{ renderAllCount += 1; renderFilters(); }};
          loadCurrentView = () => {{ loadCurrentViewCount += 1; }};
          setActiveFilter({{ dataset: {{ filter: "period_year", value: "2026" }} }});
          JSON.stringify({{
            selectedYear: state.filters.period_year,
            renderFiltersCount,
            renderAllCount,
            loadCurrentViewCount,
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"selectedYear":"2026"' in completed.stdout
    assert '"renderFiltersCount":1' in completed.stdout
    assert '"renderAllCount":1' in completed.stdout
    assert '"loadCurrentViewCount":0' in completed.stdout


def test_cost_info_cascade_options_reuse_dimension_index_without_rescanning_archives():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "cost_info";
          state.viewMode = "archives";
          state.filters.region_code = "51";
          state.filters.region_code_city = "510500";
          state.filters.period_year = "2026";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ region_code: "510500", metadata: {{ city_raw: cell("泸州市"), period: cell("2026-05") }} }},
            {{ region_code: "510500", metadata: {{ city_raw: cell("泸州市"), period: cell("2026-06") }} }},
            {{ region_code: "510600", metadata: {{ city_raw: cell("德阳市"), period: cell("2026-04") }} }},
            {{ region_code: "110000", metadata: {{ period: cell("2026-06") }} }},
          ];
          const regionFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "region_code");
          const yearFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_year");
          const monthFilter = domainConfigs.cost_info.filters.find((filter) => filter.key === "period_month");
          const originalArchiveMatchesProvince = archiveMatchesProvince;
          const originalArchiveMatchesCity = archiveMatchesCity;
          let provinceScans = 0;
          let cityScans = 0;
          archiveMatchesProvince = (...args) => {{ provinceScans += 1; return originalArchiveMatchesProvince(...args); }};
          archiveMatchesCity = (...args) => {{ cityScans += 1; return originalArchiveMatchesCity(...args); }};
          cityOptions("51", regionFilter);
          filterOptions(yearFilter);
          filterOptions(monthFilter);
          provinceScans = 0;
          cityScans = 0;
          const cityLabels = cityOptions("51", regionFilter).map((option) => option.label);
          const yearOptionsSecond = filterOptions(yearFilter);
          const monthOptionsSecond = filterOptions(monthFilter);
          JSON.stringify({{
            cityLabels,
            yearOptionsSecond,
            monthOptionsSecond,
            provinceScans,
            cityScans,
          }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"cityLabels":["全部","泸州市","德阳市"]' in completed.stdout
    assert '"yearOptionsSecond":[{"label":"全部","value":"all"},{"label":"2026","value":"2026"}]' in completed.stdout
    assert '"monthOptionsSecond":[{"label":"全部","value":"all"},{"label":"5月","value":"05"},{"label":"6月","value":"06"}]' in completed.stdout
    assert '"provinceScans":0' in completed.stdout
    assert '"cityScans":0' in completed.stdout


def test_legacy_trading_exchange_filter_value_does_not_filter_results():
    app_js = Path(__file__).parents[1] / "app" / "ui" / "app.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({str(app_js)!r}, "utf8");
        const browserBootIndex = source.indexOf('document.addEventListener("click"');
        const appCode = source.slice(0, browserBootIndex);
        const result = vm.runInNewContext(appCode + `
          state.domain = "trading";
          state.viewMode = "archives";
          state.filters.exchange_name = "江西省公共资源交易平台";
          const cell = (value) => ({{ value }});
          state.archives = [
            {{ title: "成都公告", status: "collected", metadata: {{ exchange_name: cell("成都市公共资源交易服务中心") }} }},
          ];
          state.archives = [
            {{ title: "成都公告", status: "collected", metadata: {{ exchange_name: cell("成都市公共资源交易服务中心") }} }},
            {{ title: "江西公告", status: "collected", metadata: {{ exchange_name: cell("江西省公共资源交易平台") }} }},
          ];
          const titles = filteredArchives().map((item) => item.title);
          JSON.stringify({{ titles }});
        `, {{ Intl, Date }});
        console.log(result);
        """
    )

    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)

    assert '"titles":["成都公告","江西公告"]' in completed.stdout
