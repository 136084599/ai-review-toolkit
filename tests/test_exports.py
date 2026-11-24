import csv
import io

from app.exports import issues_csv, srt_time


def test_srt_rounding_carries_into_next_minute():
    assert srt_time(59.9998)=="00:01:00,000"
    assert srt_time(3600.123)=="01:00:00,123"


def test_csv_does_not_execute_formulas_and_preserves_commas():
    job={"report":{"issues":[{"id":"issue-1","start":1,"end":3,"severity":"high","category":"=FORMULA()", "title":"含逗号,和换行\n的标题",
        "evidence":"+SUM(1,2)","suggestion":"请核对"}]}, "decisions":{"issue-1":{"status":"resolved","note":" @HYPERLINK(1)"}}}
    text=issues_csv(job)
    rows=list(csv.reader(io.StringIO(text.removeprefix('\ufeff'))))
    assert rows[1][3]=="'=FORMULA()" and rows[1][5]=="'+SUM(1,2)"
    assert rows[1][4]=="含逗号,和换行\n的标题"
    assert rows[1][8].startswith("'")
