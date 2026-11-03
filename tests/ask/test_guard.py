import pytest

from skysnap_ask.guard import ALLOWED_TABLES, UnsafeSQLError, guard_sql


def test_allowed_tables_constant():
    assert ALLOWED_TABLES == frozenset({"dim_city", "weather_hourly", "weather_rejects"})


def test_simple_select_gets_limit():
    out = guard_sql("SELECT * FROM weather_hourly")
    assert out.upper().endswith("LIMIT 200")


def test_existing_small_limit_kept():
    assert "LIMIT 5" in guard_sql("SELECT * FROM dim_city LIMIT 5").upper()


def test_large_limit_capped():
    assert "LIMIT 200" in guard_sql("SELECT * FROM dim_city LIMIT 100000").upper()


def test_custom_max_rows():
    assert guard_sql("SELECT * FROM dim_city", max_rows=7).upper().endswith("LIMIT 7")


def test_non_literal_limit_capped():
    out = guard_sql("SELECT * FROM dim_city LIMIT (SELECT 5)")
    assert out.upper().endswith("LIMIT 200")


def test_limit_with_offset_kept_and_capped():
    out = guard_sql("SELECT * FROM dim_city LIMIT 10, 99999").upper()
    assert "LIMIT 200 OFFSET 10" in out


def test_trailing_semicolon_accepted():
    assert guard_sql("SELECT * FROM dim_city;").upper().endswith("LIMIT 200")


def test_union_gets_single_trailing_limit():
    out = guard_sql("SELECT name FROM dim_city UNION ALL SELECT city FROM weather_rejects")
    assert out.upper().endswith("LIMIT 200")
    assert out.upper().count("LIMIT") == 1


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE dim_city",
        "DELETE FROM weather_hourly",
        "UPDATE dim_city SET name='x'",
        "INSERT INTO dim_city VALUES (9,'x','y',1,1)",
        "TRUNCATE weather_hourly",
        "CREATE TABLE t (a INT)",
        "GRANT ALL ON *.* TO 'x'",
        "SHOW TABLES",
        "SET @a = 1",
        "WITH t AS (SELECT 1) DELETE FROM dim_city",
    ],
)
def test_non_select_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_multiple_statements_blocked():
    with pytest.raises(UnsafeSQLError, match="exactly one statement"):
        guard_sql("SELECT 1; DROP TABLE dim_city")


def test_comment_cannot_hide_statement():
    with pytest.raises(UnsafeSQLError, match="exactly one statement"):
        guard_sql("SELECT 1 /* x */; DROP TABLE dim_city -- y")


def test_comment_is_harmless_when_single_statement():
    assert guard_sql("SELECT 1 /* ; DROP TABLE dim_city */") == (
        "SELECT 1 /* ; DROP TABLE dim_city */ LIMIT 200"
    )


def test_catalog_qualified_table_blocked():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("SELECT * FROM x.weather.dim_city")


def test_empty_blocked():
    with pytest.raises(UnsafeSQLError):
        guard_sql("   ")


def test_unknown_table_blocked():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("SELECT * FROM mysql.user")


def test_information_schema_blocked():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("SELECT * FROM information_schema.tables")


def test_schema_qualified_weather_allowed():
    out = guard_sql("SELECT * FROM weather.dim_city")
    assert "dim_city" in out


def test_other_schema_blocked():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("SELECT * FROM weather_test.dim_city")


def test_cte_allowed():
    out = guard_sql(
        "WITH t AS (SELECT city_id, MAX(temperature_c) m FROM weather_hourly GROUP BY city_id) "
        "SELECT c.name, t.m FROM t JOIN dim_city c USING(city_id)"
    )
    assert "LIMIT 200" in out.upper()


def test_cte_name_does_not_whitelist_other_tables():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("WITH t AS (SELECT 1 AS a) SELECT * FROM t, secrets")


def test_cte_body_with_unknown_table_blocked():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("WITH t AS (SELECT * FROM mysql.user) SELECT * FROM t")


def test_alias_named_like_disallowed_table_is_not_a_cte():
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql("SELECT * FROM users u")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `mysql`.`user`",
        "SELECT * FROM `secrets`",
        "SELECT * FROM DIM_CITY d JOIN Users u ON 1=1",
        "SELECT * FROM dim_city WHERE city_id IN (SELECT id FROM secrets)",
        "SELECT name FROM dim_city UNION ALL SELECT user FROM mysql.user",
        "SELECT (SELECT COUNT(*) FROM secrets) FROM dim_city",
        "SELECT * FROM (SELECT * FROM secrets) s",
    ],
)
def test_disallowed_tables_everywhere_blocked(sql):
    with pytest.raises(UnsafeSQLError, match="unknown table"):
        guard_sql(sql)


def test_backtick_and_uppercase_allowed_tables_ok():
    guard_sql("SELECT * FROM `dim_city`")
    guard_sql("SELECT * FROM DIM_CITY")
    guard_sql("SELECT * FROM `weather`.`weather_hourly`")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SLEEP(10)",
        "SELECT BENCHMARK(1000000, MD5('a'))",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT name FROM dim_city WHERE SLEEP(1) = 0",
        "SELECT IF(1, SLEEP(1), 0)",
        "SELECT GET_LOCK('a', 1)",
        "SELECT RELEASE_LOCK('a')",
        "SELECT SYSTEM_USER()",
        "SELECT USER()",
        "SELECT CURRENT_USER()",
        "SELECT CURRENT_USER",
        "SELECT DATABASE()",
        "SELECT VERSION()",
        "SELECT * FROM dim_city WHERE city_id IN (SELECT SLEEP(1))",
        "SELECT name FROM dim_city UNION SELECT VERSION()",
        "WITH t AS (SELECT SLEEP(1) s) SELECT * FROM t",
        "SELECT COALESCE(NULLIF(SLEEP(1), 1), 0)",
    ],
)
def test_banned_functions_blocked(sql):
    with pytest.raises(UnsafeSQLError, match="not allowed"):
        guard_sql(sql)


def test_case_insensitive_function_ban():
    with pytest.raises(UnsafeSQLError, match="function SLEEP is not allowed"):
        guard_sql("select sleep(1)")


def test_select_into_blocked():
    with pytest.raises(UnsafeSQLError, match="INTO is not allowed"):
        guard_sql("SELECT * FROM dim_city INTO OUTFILE '/tmp/x'")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dim_city INTO DUMPFILE '/tmp/x'",
        "select * from dim_city into   outfile '/tmp/x'",
        "SELECT * FROM dim_city INTO @v",
        "SELECT city_id INTO @v FROM dim_city",
    ],
)
def test_into_variants_blocked(sql):
    with pytest.raises(UnsafeSQLError, match="INTO is not allowed"):
        guard_sql(sql)


def test_unparseable_blocked():
    with pytest.raises(UnsafeSQLError, match="could not parse"):
        guard_sql("SELEC FROM")


def test_union_of_allowed_tables_ok():
    guard_sql("SELECT name FROM dim_city UNION SELECT city FROM weather_rejects")


def test_output_is_mysql_sql_string():
    assert isinstance(guard_sql("SELECT 1"), str)
