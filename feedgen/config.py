"""Загрузка и проверка config.toml."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Agency:
    slug: str
    name: str
    organization: str
    category: str = "агентство"
    phones: list = field(default_factory=list)
    agent_name: str = ""
    email: str = ""
    url: str = ""
    utm_source: str = ""
    projects: list = field(default_factory=list)   # пусто = все ЖК

    def wants(self, project_key):
        return not self.projects or project_key in self.projects


@dataclass
class Portal:
    """Тексты страницы со ссылками и блока для копирования."""
    developer: str = "Петербургская строительная компания"
    site: str = "https://psk-info.ru"
    phone: str = ""
    manager: str = ""
    manager_role: str = "руководитель отдела"
    support_bot: str = ""


@dataclass
class Project:
    key: str                     # латиница: часть ссылки на фид (feeds/<агентство>/<key>.xml)
    api_id: int
    name: str
    yandex_building_id: str
    yandex_house_id: str         # корпус по умолчанию
    house_ids: dict = field(default_factory=dict)   # номер корпуса в API -> yandex-house-id
    building_type: str = "монолит"
    building_section: str = "корпус {building}"
    building_labels: dict = field(default_factory=dict)   # номер корпуса в API -> как показывать (71 -> 7.1)
    label: str = "ЖК"                                     # "ЖК" или "Апарт-отель"
    deal_status: str = "первичная продажа"
    include_statuses: list = field(default_factory=lambda: ["Свободно"])
    render_count: int = 6
    show_discount: bool = False
    renovation: dict = field(default_factory=dict)

    def house_id(self, flat):
        return self.house_ids.get(str(flat.get("building") or ""), self.yandex_house_id)

    def section(self, flat):
        b = flat.get("building")
        if not b or not self.building_section:
            return ""
        return self.building_section.format(building=self.building_labels.get(str(b), b))

    @property
    def title(self):
        return f"{self.label} «{self.name}»"


@dataclass
class Config:
    base_dir: Path
    public_base_url: str
    output_dir: Path
    cache_dir: Path
    api_base: str
    site_url: str
    refresh_minutes: int
    image_size: int
    api_workers: int
    projects: list
    agencies: list
    legacy_project: str = ""
    portal: Portal = field(default_factory=Portal)

    @property
    def feeds_dir(self):
        return self.output_dir / "feeds"

    @property
    def plans_dir(self):
        return self.output_dir / "plans"

    def feed_rel(self, agency, project):
        return f"feeds/{agency.slug}/{project.key}.xml"

    def preview_rel(self, agency, project):
        return f"preview/{agency.slug}/{project.key}.html"

    def public_url(self, rel_path):
        return f"{self.public_base_url.rstrip('/')}/{rel_path.lstrip('/')}"


def load_config(path):
    path = Path(path).resolve()
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    base = path.parent
    g = raw.get("general", {})
    agencies = [Agency(**a) for a in raw.get("agency", [])]

    projects = [Project(**p) for p in raw.get("project", [])]

    slugs = [a.slug for a in agencies]
    if len(slugs) != len(set(slugs)):
        raise ValueError("В config.toml повторяется slug агентства")
    keys = [p.key for p in projects]
    if len(keys) != len(set(keys)):
        raise ValueError("В config.toml повторяется key проекта")
    for a in agencies:
        unknown = set(a.projects) - set(keys)
        if unknown:
            raise ValueError(f"Агентство {a.slug}: неизвестные ЖК {unknown}")

    return Config(
        base_dir=base,
        public_base_url=g["public_base_url"],
        output_dir=(base / g.get("output_dir", "public")).resolve(),
        cache_dir=(base / g.get("cache_dir", "cache")).resolve(),
        api_base=g.get("api_base", "https://psk-info.ru/api").rstrip("/"),
        site_url=g.get("site_url", "https://psk-info.ru").rstrip("/"),
        refresh_minutes=int(g.get("refresh_minutes", 60)),
        image_size=int(g.get("image_size", 1200)),
        api_workers=int(g.get("api_workers", 8)),
        projects=projects,
        legacy_project=g.get("legacy_project", ""),
        portal=Portal(**raw.get("portal", {})),
        agencies=agencies,
    )
