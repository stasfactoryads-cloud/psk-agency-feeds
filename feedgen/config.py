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


@dataclass
class Project:
    api_id: int
    name: str
    yandex_building_id: str
    yandex_house_id: str
    building_type: str = "монолит"
    building_section: str = ""
    deal_status: str = "первичная продажа"
    include_statuses: list = field(default_factory=lambda: ["Свободно"])
    render_count: int = 6
    show_discount: bool = False
    renovation: dict = field(default_factory=dict)


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
    project: Project
    agencies: list

    @property
    def feeds_dir(self):
        return self.output_dir / "feeds"

    @property
    def plans_dir(self):
        return self.output_dir / "plans"

    def public_url(self, rel_path):
        return f"{self.public_base_url.rstrip('/')}/{rel_path.lstrip('/')}"


def load_config(path):
    path = Path(path).resolve()
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    base = path.parent
    g = raw.get("general", {})
    agencies = [Agency(**a) for a in raw.get("agency", [])]

    slugs = [a.slug for a in agencies]
    if len(slugs) != len(set(slugs)):
        raise ValueError("В config.toml повторяется slug агентства")

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
        project=Project(**raw["project"]),
        agencies=agencies,
    )
