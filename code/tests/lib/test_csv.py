from conftest import run_tb, tb_error


def test_parse_with_header():
    src = 'import csv\nlet rows = csv.parse("name,age\\nTanisha,21\\n\\n\\"Smith, J\\",30\\n")\nsay(rows)\nsay(rows[1].name)'
    assert run_tb(src) == ['[{"name": "Tanisha", "age": "21"}, {"name": "Smith, J", "age": "30"}]', "Smith, J"]


def test_parse_ragged_rows_and_options():
    src = 'import csv\nsay(csv.parse("a,b\\n1\\n1,2,3"))\nsay(csv.parse("a;b\\n1;2", {"delimiter": ";"}))\nsay(csv.parse("1,2\\n3,4", {"header": false}))\nsay(csv.parse(""))'
    assert run_tb(src) == [
        '[{"a": "1", "b": ""}, {"a": "1", "b": "2", "column3": "3"}]',
        '[{"a": "1", "b": "2"}]',
        '[["1", "2"], ["3", "4"]]',
        "[]",
    ]


def test_stringify_maps_and_lists():
    src = 'import csv\nsay(csv.stringify([{"a": 1, "b": "x,y"}, {"a": 2, "c": nil}]))\nsay(csv.stringify([[1, 2], ["q\\"uote", true]]))'
    assert run_tb(src) == ['a,b,c\n1,"x,y",\n2,,\n', '1,2\n"q""uote",true\n']


def test_read_write_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = 'import csv\ncsv.write("o.csv", [{"ip": "1.2.3.4", "hits": 5}])\nsay(csv.read("o.csv"))\nsay(csv.read("o.csv", {"header": false}))'
    assert run_tb(src) == ['[{"ip": "1.2.3.4", "hits": "5"}]', '[["ip", "hits"], ["1.2.3.4", "5"]]']
    assert (tmp_path / "o.csv").read_text() == "ip,hits\n1.2.3.4,5\n"


def test_csv_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "I couldn't find 'nope.csv'" in str(tb_error('import csv\ncsv.read("nope.csv")'))
    assert "needs a list of maps or a list of lists, but one row is number" in str(tb_error("import csv\ncsv.stringify([1])"))
    assert "csv.stringify() needs rows to be a list" in str(tb_error('import csv\ncsv.stringify("x")'))
    assert "delimiter to be one letter" in str(tb_error('import csv\ncsv.parse("a", {"delimiter": ";;"})'))
    assert "csv.parse() needs options to be a map" in str(tb_error('import csv\ncsv.parse("a", 1)'))
