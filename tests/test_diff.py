import json

from tarjuman import ToolCall

from diwan.diff import change, listing, rows

FILE = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7\nh = 8\ni = 9\n"


def call(name, **args):
    return ToolCall("c1", name, json.dumps(args))


def test_edit_reads_the_file_for_line_numbers_and_context():
    ch = change(call("edit", path="x.py", old="e = 5", new="e = 50"), lambda p: FILE)
    r, adds, dels = rows(ch.before, ch.after)
    assert (adds, dels) == (1, 1)
    kinds = [(x.kind, x.old, x.new) for x in r]
    assert kinds == [("ctx", 2, 2), ("ctx", 3, 3), ("ctx", 4, 4), ("del", 5, None), ("add", None, 5),
                     ("ctx", 6, 6), ("ctx", 7, 7), ("ctx", 8, 8)]


def test_changed_words_are_marked_inside_a_replaced_line():
    r, _, _ = rows("total += len(hits[i][1])\n", "total += size\n")
    removed = next(s for s in r if s.kind == "del").spans
    added = next(s for s in r if s.kind == "add").spans
    assert ("total += ", False) in removed
    assert any(changed and "len" in text for text, changed in removed)
    assert ("size", True) in added


def test_far_apart_changes_become_hunks_with_a_gap():
    after = FILE.replace("a = 1", "a = 10").replace("i = 9", "i = 90")
    r, _, _ = rows(FILE, after, context=1)
    assert [x.kind for x in r].count("gap") == 1


def test_write_over_an_existing_file_diffs_against_it():
    ch = change(call("write", path="x.py", content=FILE.replace("b = 2", "b = 3")), lambda p: FILE)
    assert not ch.created
    _, adds, dels = rows(ch.before, ch.after)
    assert (adds, dels) == (1, 1)


def test_write_of_a_new_file_is_all_additions():
    ch = change(call("write", path="new.py", content="x = 1\ny = 2\n"), lambda p: None)
    assert ch.created
    assert rows(ch.before, ch.after)[1:] == (2, 0)


def test_a_replayed_edit_is_undone_in_memory_to_find_the_before():
    done = FILE.replace("e = 5", "e = 50")
    ch = change(call("edit", path="x.py", old="e = 5", new="e = 50"), lambda p: done, applied=True)
    assert ch.before == FILE and ch.after == done


def test_unreadable_file_falls_back_to_the_snippets():
    ch = change(call("edit", path="gone.py", old="x", new="y"), lambda p: None)
    assert (ch.before, ch.after) == ("x", "y")


def test_other_tools_and_bad_arguments_have_no_change():
    assert change(call("bash", command="ls"), lambda p: None) is None
    assert change(ToolCall("c1", "edit", "{not json"), lambda p: None) is None


def test_long_diffs_are_cut_with_a_count():
    r, adds, _ = rows("", "\n".join(f"line {i}" for i in range(200)), limit=10)
    assert len(r) == 11 and r[-1].kind == "gap" and "190 more lines" in r[-1].text
    assert adds == 200


def test_a_new_file_is_listed_not_marked_as_added():
    r = listing("x = 1\ny = 2\n")
    assert [(x.kind, x.new, x.text) for x in r] == [("ctx", 1, "x = 1"), ("ctx", 2, "y = 2")]
