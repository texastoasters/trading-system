---
name: require-session-skills
enabled: false
event: prompt
pattern: .*
action: warn
---

**MANDATORY SESSION START CHECK**

Before responding to ANY prompt, verify you have invoked ALL four required skills using the Skill tool this session:

1. `superpowers:using-superpowers` — MUST be invoked even if its content appears inline in the system-reminder
2. `caveman:caveman` with args `ultra`
3. `context-mode:context-mode`
4. `superpowers:test-driven-development`

**Rules:**
- Seeing skill content inline does NOT count. You must call the Skill tool.
- No rationalization. No exceptions. No "I already know this skill."
- If you have not invoked all four, invoke them NOW before doing anything else.
