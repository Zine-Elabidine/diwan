from tarjuman import Message, Text

from diwan.log import Log


def test_callers_get_their_own_lists(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("one"))
    log.append("mask", {"entries": {"out:c1": "[cleared]"}})
    mine = log.messages()
    mine.append(Message.user("not logged"))
    log.masked()["out:c2"] = "not logged"
    log.mask_points().append(99)
    assert [m.text for m in log.messages()] == ["one"]
    assert log.masked() == {"out:c1": "[cleared]"} and log.mask_points() == [1]


def test_the_cached_branch_follows_appends_and_moves(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    first = log.add_message(Message.user("one"))
    assert [m.text for m in log.messages()] == ["one"]           # cache built here
    log.add_message(Message("assistant", [Text("two")]))
    log.append("mask", {"entries": {"out:c1": "x"}})
    log.add_message(Message.user("three"))
    assert [m.text for m in log.messages()] == ["one", "two", "three"]
    assert log.mask_points() == [2]

    log.head = first                                                # branch from an older event
    log.add_message(Message.user("other"))
    assert [m.text for m in log.messages()] == ["one", "other"]
    assert log.masked() == {} and log.mask_points() == []

    again = Log.load(log.path)                                      # same answer from disk
    assert [m.text for m in again.messages()] == ["one", "other"]
