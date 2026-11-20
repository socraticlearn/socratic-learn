# Pi Adapter

The checked-in [adapter source](skills/socratic-learn/SKILL.md) is readable in
this repository, but **do not copy it unchanged** to a project-local Pi skill:
its links have one extra directory level. The adjacent
[project-install template](skills/socratic-learn/SKILL.project.md.template) is
root-ready for the documented destination
`<project>/.pi/skills/socratic-learn/SKILL.md`.

The installation is namespaced and fail-closed: it creates the canonical
package at `<project>/.socratic-learn/` and aborts if that directory or the Pi
skill directory already exists. It does not write to `<project>/AGENTS.md`.

From this repository root, install with PowerShell:

```powershell
$Project = Resolve-Path "<project>"
$PackageDirectory = Join-Path $Project ".socratic-learn"
$SkillDirectory = Join-Path $Project ".pi\skills\socratic-learn"
$SkillFile = Join-Path $SkillDirectory "SKILL.md"
if (Test-Path $PackageDirectory) {
  throw "Installation aborted: $PackageDirectory already exists."
}
if (Test-Path $SkillDirectory) {
  throw "Installation aborted: $SkillDirectory already exists."
}
New-Item -ItemType Directory $PackageDirectory | Out-Null
Copy-Item AGENTS.md $PackageDirectory
Copy-Item -Recurse core,curricula $PackageDirectory
New-Item -ItemType Directory -Force $SkillDirectory | Out-Null
Copy-Item adapters\pi\skills\socratic-learn\SKILL.project.md.template `
  $SkillFile
```

Or with a POSIX shell:

```sh
project="<project>"
package_dir="$project/.socratic-learn"
skill_dir="$project/.pi/skills/socratic-learn"
skill_file="$skill_dir/SKILL.md"
if [ -e "$package_dir" ]; then
  echo "Installation aborted: $package_dir already exists." >&2
  exit 1
fi
if [ -e "$skill_dir" ]; then
  echo "Installation aborted: $skill_dir already exists." >&2
  exit 1
fi
mkdir "$package_dir"
cp AGENTS.md "$package_dir/"
cp -R core curricula "$package_dir/"
mkdir -p "$skill_dir"
cp adapters/pi/skills/socratic-learn/SKILL.project.md.template \
  "$skill_file"
```

The adapter skill contains only loader and capability-mapping guidance; the
pedagogical source remains in the namespaced canonical package.

## Project agent integration

The installer does not overwrite or edit the project's `AGENTS.md`. If the
project already has one, manually add a short pointer such as:

```markdown
Before a learning session, read the namespaced
[learning tutor entry](.socratic-learn/AGENTS.md).
```

If the project has no `AGENTS.md`, you may create one containing that same
short loader. This integration is explicit and separate from installation.

The skill delegates to the universal [agent entry](../../AGENTS.md) and canonical [pedagogy](../../core/pedagogy.md), [session protocol](../../core/session-protocol.md), [assessment](../../core/assessment.md), and [evidence policy](../../core/evidence-policy.md). Pi extensions are optional because the skill documents a conversational or built-in fallback for each mapping.

The adapter exposes the same subject-neutral protocol; mathematics and AI are
included starter curricula rather than adapter limits.

## Model selection and quota errors

The Pi host owns model selection, authentication, and provider quota. Socratic
Learn adds the teaching protocol but does not override those settings.

Run `/model` inside Pi to open the model picker. A normal selection applies to
the current session. To make the highlighted model the default for new
sessions, press `Ctrl+S` in the picker. Pi records model changes in each session,
so resuming a session restores its latest model without replacing the saved
default.

Changing models does not replay a request that already failed. After selecting
a different model, submit a new message such as `continue`. If Pi reports HTTP
`429`, inspect the provider and model named in the error. That account or
project has no quota available for the request. Choose a model with available
quota, run `/login` to configure another provider, or wait for the reset time
reported by the provider. Selecting another model cannot add quota or bypass
provider restrictions.

Socratic Learn neither reads nor stores Pi credentials. Pi keeps them in its
own configuration and only lists models for providers with usable
authentication.
