import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from seaqa_ai.agent.skills.loader import SkillLoadError, load_skill_spec
from seaqa_ai.agent.skills.spec import AgentSkillSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteKey:
    source_type: str
    event_type: str


class SkillRegistry:
    def __init__(self, skills: list[AgentSkillSpec]):
        self._skills = {}
        self._route_map = {}
        for skill in skills:
            if skill.id in self._skills:
                raise SkillLoadError(f'Duplicate skill id: {skill.id}')
            self._skills[skill.id] = skill
            for source_type in skill.source_types:
                for event_type in skill.event_types:
                    route_key = RouteKey(source_type=source_type, event_type=event_type)
                    if route_key in self._route_map:
                        existing = self._route_map[route_key]
                        raise SkillLoadError(
                            f'Duplicate skill route ({source_type}, {event_type}) '
                            f'for {existing.id} and {skill.id}.'
                        )
                    self._route_map[route_key] = skill

    @classmethod
    def from_directory(cls, directory: str | Path) -> 'SkillRegistry':
        root = Path(directory)
        if not root.exists():
            raise SkillLoadError(f'Skill directory does not exist: {root}')

        skill_files = sorted(root.glob('*/SKILL.md'))
        skills = []
        for skill_file in skill_files:
            skill_spec = load_skill_spec(skill_file)
            skills.append(skill_spec)
        if not skills:
            raise SkillLoadError(f'No skills found in {root}')
        return cls(skills)

    def all(self) -> list[AgentSkillSpec]:
        return list(self._skills.values())

    def get(self, skill_id: str) -> AgentSkillSpec | None:
        return self._skills.get(skill_id)

    def resolve(self, source_type: str, event_type: str) -> AgentSkillSpec | None:
        return self._route_map.get(RouteKey(source_type=source_type, event_type=event_type))


@lru_cache(maxsize=1)
def get_default_skill_registry() -> SkillRegistry:
    skills_root = Path(__file__).resolve().parent / 'builtins'
    registry = SkillRegistry.from_directory(skills_root)
    logger.info('Loaded %s agent skills from %s', len(registry.all()), skills_root)
    return registry
