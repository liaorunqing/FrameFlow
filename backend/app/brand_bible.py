"""Project-scoped visual truth locks for reliable commercial generation."""

from __future__ import annotations

from datetime import datetime

from .schemas import AssetKind, BrandBible, Project, SubjectBible
from .vision import AssetVisualProfile


def _profile_features(
    asset_ids: list[str],
    profiles: dict[str, AssetVisualProfile],
) -> list[str]:
    values: list[str] = []
    for asset_id in asset_ids:
        profile = profiles.get(asset_id)
        if profile:
            values.extend(profile.immutable_features)
            values.extend(profile.geometry_or_identity)
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:10]


def draft_brand_bible(
    project: Project,
    *,
    profiles: dict[str, AssetVisualProfile] | None = None,
) -> BrandBible:
    """Create a conservative draft; it deliberately avoids invented visual facts."""
    profiles = profiles or {}
    grouped = {
        kind: [item.id for item in project.assets if item.kind == kind]
        for kind in AssetKind
    }
    product_features = _profile_features(grouped[AssetKind.product], profiles) or [
        f"{project.product_name} 的轮廓、颜色、比例与核心结构保持与商品参考图一致",
    ]
    character_features = _profile_features(grouped[AssetKind.character], profiles) or [
        "人物脸部、年龄感、发型与服装保持与人物参考图一致",
    ]
    scene_features = _profile_features(grouped[AssetKind.scene], profiles) or [
        "场景空间布局、主要家具与光线方向保持与场景参考图一致",
    ]
    return BrandBible(
        product=SubjectBible(
            asset_ids=grouped[AssetKind.product],
            immutable_features=product_features,
            required_evidence=list(project.selling_points),
            prohibited_changes=["不得替换商品类别", "不得改变商品核心结构", "不得生成商品上的假文字"],
        ),
        character=SubjectBible(
            asset_ids=grouped[AssetKind.character],
            immutable_features=character_features,
            prohibited_changes=["不得换脸、改变年龄感或更换服装", "不得出现重复人物"],
        ),
        scene=SubjectBible(
            asset_ids=grouped[AssetKind.scene],
            immutable_features=scene_features,
            prohibited_changes=["不得切换到不相关空间", "不得改变主要光线方向"],
        ),
        brand=SubjectBible(
            asset_ids=grouped[AssetKind.brand],
            required_evidence=["Logo、价格、CTA 仅由后期确定性叠加"],
            prohibited_changes=["不得由生成模型绘制 Logo、价格或促销文字"],
        ),
        # Reference photos cannot establish marketing claims. Claims remain
        # empty until a human supplies separate verifiable evidence.
        mandatory_claims=[],
        visual_tone=[project.style, "真实生活感", "克制表演", "电影化自然光"],
        global_prohibitions=[
            "不得生成水印、乱码、假 Logo 或无依据营销承诺",
            "每个镜头只完成一个清晰动作，不插入无关事件",
        ],
        review_checklist=[
            "商品是否与参考图为同一商品", "人物身份与服装是否连续",
            "场景与光线是否连续", "手部、接触与动作是否符合物理", "品牌文字是否仅由后期叠加",
        ],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def bible_constraints(project: Project) -> list[str]:
    bible = project.brand_bible
    if not bible or bible.status != "approved":
        return []
    groups = (bible.product, bible.character, bible.scene, bible.brand)
    values = [
        *[item for group in groups for item in group.immutable_features],
        *[item for group in groups for item in group.prohibited_changes],
        *bible.global_prohibitions,
    ]
    return list(dict.fromkeys(value for value in values if value))
