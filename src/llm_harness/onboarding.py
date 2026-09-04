from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .role_profiles import ProjectRoleProfile, RoleSelection


@dataclass(frozen=True)
class RolePreset:
    preset_id: str
    title_en: str
    title_ru: str
    description_en: str
    description_ru: str
    supervisor: RoleSelection
    worker: RoleSelection
    critic: RoleSelection | None

    @property
    def required_agents(self) -> tuple[str, ...]:
        selections = (self.supervisor, self.worker, self.critic)
        return tuple(dict.fromkeys(item.agent for item in selections if item is not None))

    def title(self, locale: str) -> str:
        return self.title_ru if locale == "ru" else self.title_en

    def description(self, locale: str) -> str:
        return self.description_ru if locale == "ru" else self.description_en

    def profile(self, max_attempts: int = 3) -> ProjectRoleProfile:
        return ProjectRoleProfile(self.supervisor, self.worker, self.critic, max_attempts)


ROLE_PRESETS = (
    RolePreset(
        "codex_trio",
        "Codex for all roles",
        "Codex во всех ролях",
        "One installed Codex ACP adapter performs Supervisor, Worker, and Critic turns with separate role sessions.",
        "Один адаптер Codex ACP выполняет роли Supervisor, Worker и Critic в раздельных сессиях.",
        RoleSelection("codex-acp", None, "acp"),
        RoleSelection("codex-acp", None, "acp"),
        RoleSelection("codex-acp", None, "acp"),
    ),
    RolePreset(
        "codex_claude_deepseek",
        "Codex + Claude + DeepSeek",
        "Codex + Claude + DeepSeek",
        "Codex supervises, Claude edits, and Qwen Code connects to a DeepSeek model for independent criticism.",
        "Codex управляет, Claude вносит изменения, а Qwen Code подключается к модели DeepSeek для независимой критики.",
        RoleSelection("codex-acp", None, "acp"),
        RoleSelection("claude-acp", None, "acp"),
        RoleSelection("qwen-code", "deepseek-chat", "acp"),
    ),
    RolePreset(
        "codex_claude_gemini",
        "Codex + Claude + Gemini",
        "Codex + Claude + Gemini",
        "Codex supervises, Claude edits, and Gemini provides an independent final review.",
        "Codex управляет, Claude вносит изменения, а Gemini независимо проверяет результат.",
        RoleSelection("codex-acp", None, "acp"),
        RoleSelection("claude-acp", None, "acp"),
        RoleSelection("gemini", None, "acp"),
    ),
)


@dataclass(frozen=True)
class OnboardingProgress:
    project_ready: bool
    agents_ready: bool
    roles_ready: bool
    missing_agents: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.project_ready and self.agents_ready and self.roles_ready


def role_preset(preset_id: str) -> RolePreset:
    normalized = preset_id.strip().casefold()
    for preset in ROLE_PRESETS:
        if preset.preset_id == normalized:
            return preset
    raise ValueError(f"Unknown role preset: {preset_id}")


def onboarding_progress(
    profile: ProjectRoleProfile | None,
    catalog: Mapping[str, Any],
) -> OnboardingProgress:
    """Setup is done when the roles are assigned and their agents are on this machine.

    Whether the vendor login works is deliberately not asserted here: nothing can
    know that without running the agent, and the first real task answers it.
    """
    if profile is None:
        return OnboardingProgress(True, False, False)

    selections = {
        "supervisor": profile.supervisor,
        "worker": profile.worker,
        "critic": profile.critic,
    }
    agents = {
        str(item.get("name", "")).casefold(): item
        for item in catalog.get("agents", [])
        if isinstance(item, Mapping)
    }
    missing = tuple(
        role
        for role, selection in selections.items()
        if selection is not None
        and not bool(agents.get(selection.agent.casefold(), {}).get("available"))
    )
    return OnboardingProgress(
        project_ready=True,
        agents_ready=not missing,
        roles_ready=True,
        missing_agents=missing,
    )
