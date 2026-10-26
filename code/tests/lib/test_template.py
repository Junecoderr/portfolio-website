"""template library: values, sections, filters, escaping."""

import pytest

import tb
from conftest import run_tb, tb_error


def test_values_and_escaping():
    src = '''import template
let v = {"name": "Tanisha <admin>", "n": 3, "ok": true, "none": nil, "user": {"email": "t@example.test"}}
say(template.render("Hi {{name}}! n={{n}} ok={{ok}} none=[{{none}}] missing=[{{missing}}]", v))
say(template.render("{{{name}}} | {{&name}} | {{name|raw}}", v))
say(template.render("{{name}}", v, {"escape": false}))
say(template.render("{{user.email}} {{user.missing}} {{ user.email | upper }}", v))
say(template.escape("a<b"), len(template.filters) > 10)
'''
    assert run_tb(src) == [
        "Hi Tanisha &lt;admin&gt;! n=3 ok=true none=[] missing=[]",
        "Tanisha <admin> | Tanisha <admin> | Tanisha <admin>",
        "Tanisha <admin>",
        "t@example.test  T@EXAMPLE.TEST",
        "a&lt;b true",
    ]


def test_sections_lists_maps_and_inverted():
    src = '''import template
let v = {"items": [{"name": "a", "qty": 1}, {"name": "b", "qty": 2}], "tags": ["x", "y"], "empty": [], "flag": false, "who": {"name": "Sam"}}
say(template.render("{{#items}}{{name}}:{{qty}};{{/items}}", v))
say(template.render("{{#tags}}[{{.}}]{{/tags}}", v))
say(template.render("{{#empty}}never{{/empty}}{{^empty}}none{{/empty}} {{^flag}}off{{/flag}}{{#flag}}on{{/flag}} {{^missing}}gone{{/missing}}", v))
say(template.render("{{#who}}{{name}} has {{tags|len}} tags{{/who}}", v))
say(template.render("{{#tags}}{{.}}{{^last}}, {{/last}}{{/tags}}", v))
'''
    assert run_tb(src) == ["a:1;b:2;", "[x][y]", "none off gone", "Sam has 2 tags", "x, y, "]


def test_standalone_lines_are_removed():
    src = '''import template
let t = "Items:\\n{{#items}}\\n- {{.}}\\n{{/items}}\\n{{! a comment }}\\nDone"
say(template.render(t, {"items": [1, 2]}))
'''
    assert run_tb(src) == ["Items:\n- 1\n- 2\nDone"]


def test_filters():
    src = '''import template
let v = {"s": "  hello world  ", "n": 3.14159, "big": 1234567, "xs": [3, 1, 2], "ts": 0, "e": "", "f": 2.5, "url": "a b&c"}
say(template.render("{{s|trim|upper}}|{{s|trim|title}}|{{s|trim|capitalize}}|{{s|trim|truncate:8}}|{{s|trim|len}}", v))
say(template.render("{{n|round:2}} {{n|round}} {{big|number}} {{f|number:1}} {{xs|sort|join:-}} {{xs|first}} {{xs|last}} {{xs|reverse|join}}", v))
say(template.render("[{{e|default:none}}] [{{missing|default:'n/a'}}] {{missing|yes_no}} {{xs|yes_no:has items,empty}} {{e|yes_no}}", v))
say(template.render("{{xs|json}} {{url|urlencode}} {{n|pad_left:10}}| {{s|trim|pad_right:14}}|", v))
say(len(template.render("{{ts|date:%Y}}", v)) == 4)
'''
    assert run_tb(src) == [
        "HELLO WORLD|Hello World|Hello world|hello...|11",
        "3.14 3 1,234,567 2.5 1-2-3 3 2 2, 1, 3",
        "[none] [n/a] no has items yes",
        "[3, 1, 2] a%20b%26c    3.14159| hello world   |",
        "true",
    ]


def test_file_and_check_and_names(tmp_path):
    path = str(tmp_path / "page.html").replace("\\", "/")
    (tmp_path / "page.html").write_text("<h1>{{title}}</h1>\n{{#rows}}<li>{{.}}</li>{{/rows}}\n", encoding="utf-8")
    src = f'''import template
say(template.file("{path}", {{"title": "A & B", "rows": [1]}}))
say(template.check("{{{{#a}}}} {{{{/a}}}}"), template.check("{{{{#a}}}}"), template.check("{{{{x|nope}}}}"))
say(template.names("{{{{a}}}} {{{{#b}}}}{{{{c}}}}{{{{/b}}}} {{{{d.e}}}} {{{{^f}}}}{{{{/f}}}}"))
'''
    assert run_tb(src) == [
        "<h1>A &amp; B</h1>\n<li>1</li>\n",
        '[] ["line 1: the section {{#a}} opened on line 1 is never closed with {{/a}}"] ["line 1: I don\'t know the filter \'nope\'. Filters: capitalize, date, default, escape, first, join, json, last, len, length, lower, number, pad_left, pad_right, reverse, round, sort, title, trim, truncate, upper, urlencode, yes_no, raw"]',
        '["a", "b", "d", "f"]',
    ]


@pytest.mark.parametrize("src,message", [
    ('template.render("{{#a}}x", {})', "the section {{#a}} opened on line 1 is never closed"),
    ('template.render("{{/a}}", {})', "closes a section that was never opened"),
    ('template.render("{{#a}}{{/b}}", {})', "doesn't match the open section {{#a}}"),
    ('template.render("{{x|shout}}", {})', "I don't know the filter 'shout'"),
    ('template.render("{{x|round}}", {"x": "abc"})', "the filter 'round' failed on {{x}}"),
    ('template.render("{{}}", {})', "{{ }} needs a name inside"),
    ('template.render("{{> part}}", {})', "partials ({{> name}}) aren't supported"),
    ('template.render("a\\n{{{x}}", {})', "on line 2 of the template, {{{ needs }}}"),
    ('template.render(5, {})', "template.render() needs text to be text"),
    ('template.render("x", 5)', "needs values to be a map or a list or nil"),
    ('template.render("x", {}, {"strict": true})', 'doesn\'t know the option "strict"'),
    ('template.file("nope.html", {})', "I couldn't find 'nope.html'"),
])
def test_template_errors(src, message):
    assert message in str(tb_error("import template\n" + src))


def test_error_has_template_line():
    err = tb_error('import template\ntemplate.render("a\\nb\\n{{#s}}", {})')
    assert err.fields["template_line"] == 3 and err.fields["reason"] == "template"


def test_docs():
    info = tb.describe_library("template")
    assert info["doc"] and all(fn["doc"] for fn in info["functions"])
