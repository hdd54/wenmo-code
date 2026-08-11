"""Granular, auditable permission policy for tools, paths, and commands."""

from dataclasses import dataclass
import fnmatch
import json
import os
import re

from tenant_state import tenant_file


VALID_EFFECTS = {"allow", "ask", "deny"}


@dataclass(frozen=True)
class PermissionDecision:
    effect: str
    reason: str
    rule: str = ""


DEFAULT_POLICY = {
    "default": "ask",
    "tools": {
        "terminal": "ask",
        "plugin_terminal_*": "ask",
        "plugin_workspace_*": "allow",
    },
    "commands": [
        {"pattern": r"^(git\s+(status|diff|log|show)|rg\b|Get-ChildItem\b)", "effect": "allow"},
        {"pattern": r"\b(git\s+push\s+--force|git\s+reset\s+--hard|Remove-Item\b.*-Recurse|rm\s+-rf)\b", "effect": "deny"},
    ],
    "paths": [
        {"pattern": ".env", "effect": "deny"},
        {"pattern": "**/.env", "effect": "deny"},
        {"pattern": "*secret*", "effect": "deny"},
        {"pattern": "**/*secret*", "effect": "deny"},
        {"pattern": "*credential*", "effect": "deny"},
        {"pattern": "**/*credential*", "effect": "deny"},
    ],
}


def normalize_policy(policy):
    raw = policy if isinstance(policy, dict) else {}
    raw_tools = raw.get("tools")
    tools = {}
    if isinstance(raw_tools, dict):
        for pattern, effect in list(raw_tools.items())[:200]:
            pattern = str(pattern).strip()[:200]
            if pattern and effect in VALID_EFFECTS:
                tools[pattern] = effect
    if not tools:
        tools = dict(DEFAULT_POLICY["tools"])

    def clean_rules(value, defaults, regex_rules=False):
        source = value if isinstance(value, list) and value else defaults
        cleaned = []
        for rule in source[:200]:
            if not isinstance(rule, dict) or rule.get("effect") not in VALID_EFFECTS:
                continue
            pattern = str(rule.get("pattern") or "").strip()[:500]
            if not pattern:
                continue
            if regex_rules:
                try:
                    re.compile(pattern)
                except re.error:
                    continue
                # Reject the most common catastrophic-backtracking forms and
                # backreferences. Permission checks run on every tool call.
                if re.search(r"\\[1-9]|\([^)]*[+*][^)]*\)\s*[+*{]", pattern):
                    continue
            cleaned.append({"pattern": pattern, "effect": rule["effect"]})
        return cleaned

    result = {
        "default": raw.get("default", DEFAULT_POLICY["default"]),
        "tools": tools,
        "commands": clean_rules(raw.get("commands"), DEFAULT_POLICY["commands"], True),
        "paths": clean_rules(raw.get("paths"), DEFAULT_POLICY["paths"]),
    }
    if result["default"] not in VALID_EFFECTS:
        result["default"] = "deny"
    return result


def _tool_aliases(tool_name):
    name = str(tool_name or "")
    aliases = [name]
    if name.startswith("plugin_"):
        bits = name.split("_")
        if len(bits) > 1:
            aliases.append(bits[1])
    return aliases


def _tool_effect(tool_name, rules):
    matches = []
    for pattern, effect in (rules or {}).items():
        if effect not in VALID_EFFECTS:
            continue
        for alias in _tool_aliases(tool_name):
            if alias == pattern:
                matches.append((effect, pattern, True))
            elif fnmatch.fnmatchcase(alias, pattern):
                matches.append((effect, pattern, False))
    for effect in ("deny", "ask", "allow"):
        exact = next(((item[0], item[1]) for item in reversed(matches)
                      if item[0] == effect and item[2]), None)
        wildcard = next(((item[0], item[1]) for item in reversed(matches)
                         if item[0] == effect and not item[2]), None)
        if exact or wildcard:
            return exact or wildcard
    return None


def _matching_effect(value, rules, regex=False):
    matches = []
    for rule in rules or []:
        if not isinstance(rule, dict) or rule.get("effect") not in VALID_EFFECTS:
            continue
        pattern = str(rule.get("pattern") or "")
        try:
            if regex:
                matched = bool(re.search(pattern, value, re.IGNORECASE))
            else:
                matched = (fnmatch.fnmatchcase(value, pattern) or
                           (pattern.startswith("**/") and fnmatch.fnmatchcase(value, pattern[3:])))
        except re.error:
            matched = False
        if matched:
            matches.append((rule["effect"], pattern))
    if not matches:
        return None
    # A deny can never be weakened by a broader allow rule.
    for effect in ("deny", "ask", "allow"):
        for matched_effect, pattern in reversed(matches):
            if matched_effect == effect:
                return effect, pattern
    return None


def evaluate_permission(tool_name, arguments, policy=None):
    """Evaluate command/path rules before the broader tool/default rule."""
    policy = normalize_policy(policy)
    arguments = arguments if isinstance(arguments, dict) else {}
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
    command_match = _matching_effect(command, policy["commands"], regex=True) if command else None

    path_values = []
    path_keys = {
        "path", "file", "files", "filename", "directory", "cwd", "workdir", "destination",
        "source", "target", "image", "input", "output", "script", "repo_path",
    }

    def collect_paths(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in path_keys:
                    candidates = item if isinstance(item, list) else [item]
                    for candidate in candidates[:200]:
                        if not isinstance(candidate, str) or not candidate.strip():
                            continue
                        normalized = candidate.replace("\\", "/").strip()
                        if normalized.startswith(("data:", "http://", "https://")):
                            continue
                        while normalized.startswith("./"):
                            normalized = normalized[2:]
                        path_values.append(normalized[:2000])
                if isinstance(item, (dict, list)):
                    collect_paths(item)
        elif isinstance(value, list):
            for item in value[:200]:
                collect_paths(item)

    collect_paths(arguments)
    path_matches = [_matching_effect(value, policy["paths"], regex=False) for value in path_values]
    path_matches = [match for match in path_matches if match]
    tool = _tool_effect(tool_name, policy["tools"])
    deny_sources = []
    if command_match and command_match[0] == "deny":
        deny_sources.append(("command pattern", command_match[1]))
    deny_path = next((item for item in path_matches if item[0] == "deny"), None)
    if deny_path:
        deny_sources.append(("path pattern", deny_path[1]))
    if tool and tool[0] == "deny":
        deny_sources.append(("tool rule", tool[1]))
    if deny_sources:
        reason, rule = deny_sources[0]
        return PermissionDecision("deny", reason, rule)

    if command_match:
        return PermissionDecision(command_match[0], "command pattern", command_match[1])
    for effect in ("ask", "allow"):
        match = next((item for item in path_matches if item[0] == effect), None)
        if match:
            return PermissionDecision(effect, "path pattern", match[1])
    if tool:
        return PermissionDecision(tool[0], "tool rule", tool[1])
    return PermissionDecision(policy["default"], "default rule", "default")


def load_runtime_policy(settings_path=None):
    settings_path = settings_path or tenant_file("settings.json")
    try:
        with open(settings_path, encoding="utf-8") as handle:
            settings = json.load(handle)
    except Exception:
        settings = {}
    explicit = settings.get("permission_policy")
    if isinstance(explicit, dict):
        return normalize_policy(explicit)
    legacy = settings.get("permissions") or {}
    policy = normalize_policy(None)
    policy["tools"] = dict(policy["tools"])
    policy["tools"]["terminal"] = legacy.get("run_command", "ask")
    policy["tools"]["plugin_terminal_*"] = legacy.get("run_command", "ask")
    policy["tools"]["plugin_workspace_*"] = legacy.get("write_files", "allow")
    return policy
