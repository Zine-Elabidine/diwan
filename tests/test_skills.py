"""Skills: folders with a SKILL.md; names and descriptions in the prompt, files read on demand."""

from diwan import skills
from diwan.paths import Access, PathPolicy


def skill(folder, name, description="Does a thing.", body="Step 1."):
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n"
                                     f"metadata:\n  version: 1\n---\n\n{body}\n")


def test_found_in_the_project_first_then_home(tmp_path):
    project, home = tmp_path / "p", tmp_path / "home"
    skill(project / ".claude" / "skills" / "deploy", "deploy", "Project deploy steps.")
    skill(home / ".diwan" / "skills" / "deploy", "deploy", "My own deploy steps.")
    skill(home / ".claude" / "skills" / "review", "review", "x " * 400)
    (home / ".claude" / "skills" / "broken").mkdir()
    (home / ".claude" / "skills" / "broken" / "SKILL.md").write_text("no front matter")
    found = skills.find(project, home)
    assert [s.name for s in found] == ["deploy", "review"]
    assert found[0].description == "Project deploy steps."
    assert len(found[1].description) <= skills.DESCRIPTION_MAX + 3
    text = skills.prompt_section(found)
    assert "- deploy: Project deploy steps." in text and str(found[0].path) in text
    assert skills.prompt_section([]) == ""


def test_skill_files_are_read_without_asking_even_in_dot_diwan(tmp_path):
    home = tmp_path / "home"
    skill(home / ".diwan" / "skills" / "mine", "mine")
    (home / ".diwan" / "env").write_text("KEY=secret")
    found = skills.find(tmp_path / "p", home)
    policy = PathPolicy(tmp_path / "p", home=home, readable=skills.readable(found))
    assert policy.check(str(found[0].path), write=False) is Access.INSIDE
    assert policy.check(str(found[0].path), write=True) is Access.DENIED     # never written
    assert policy.check(str(home / ".diwan" / "env"), write=False) is Access.DENIED
