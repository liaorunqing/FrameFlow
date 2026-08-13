"""A conservative, factual 30-second template for the plush-bear research run."""

from __future__ import annotations

from .schemas import CreativePlan, Project, Shot, StorySequence


def build_plush_bear_30s_plan(project: Project) -> CreativePlan:
    """Five six-second shots. No invented names, locations or product claims."""
    shots = [
        Shot(id="shot-01", index=1, title="回到客厅", duration=6, purpose="建立真实日常开场",
             narrative_beat="放学后回到同一间自然窗光客厅", visual="儿童从画面左侧走入木地板与蓝白地毯的客厅，白色毛绒熊以坐姿留在棕色沙发附近。",
             camera="竖版中广景，轻微跟随，视线从左向右", action="儿童放慢脚步，注意到客厅里的白色毛绒熊。", voiceover="一天结束，熟悉的客厅里，陪伴已经在等候。", on_screen_text="",
             continuity_anchor="金色短发、挖掘机图案上衣；白色圆耳、黑圆眼、棕鼻毛绒熊；棕色沙发、蓝白地毯、自然窗光。",
             transition="保留儿童向右的视线和迈步动作，环境声持续。", prompt="Live-action documentary advertising shot in the supplied living room. A young child with short blond hair and an excavator-print top returns from the left, notices the supplied white seated plush bear near the brown sofa. Natural window light, restrained performance, no text or logo.", model_hint="economy"),
        Shot(id="shot-02", index=2, title="发现陪伴", duration=6, purpose="让产品自然进入故事",
             narrative_beat="注意变成靠近", visual="儿童沿着蓝白地毯走近白色毛绒熊，客厅摆设和光线完全连续。", camera="中景推近，保持左到右屏幕方向", action="儿童在毛绒熊前停下，轻轻伸手靠近而不遮挡其脸部。", voiceover="一个小小的目光相遇，让脚步慢了下来。", on_screen_text="",
             continuity_anchor="保持上一镜头的左到右运动、同一上衣、同一白色毛绒熊坐姿。", transition="手部动作在画面下方延续到下一镜头，保留轻微室内环境声。", prompt="Continuous live-action shot in the exact supplied living room. The same child approaches the same white plush bear on the blue-and-white rug, stops and gently reaches toward it without covering its round black eyes or brown nose. Natural physics, no text or logo.", model_hint="story"),
        Shot(id="shot-03", index=3, title="一起游戏", duration=6, purpose="展示自然互动",
             narrative_beat="从靠近到共同游戏", visual="儿童坐到蓝白地毯上，将白色毛绒熊保持坐姿放在身边，画面里只有一个儿童。", camera="低机位中近景，稳定观察", action="儿童把毛绒熊安稳放在身旁，拿起一件画外的普通玩具开始游戏。", voiceover="游戏开始时，它只是安静地坐在身边。", on_screen_text="",
             continuity_anchor="毛绒熊始终是白色、圆耳、黑圆眼、棕鼻且坐姿；儿童服装与客厅不变。", transition="儿童低头的动作与轻微玩具环境声连接下一镜头。", prompt="Natural live-action family-documentary shot. The same child sits on the blue-and-white rug and places the same white round-eared plush bear in a seated pose beside them, then begins a simple off-screen toy activity. Keep the bear geometry visible, one child only, no text or logo.", model_hint="story"),
        Shot(id="shot-04", index=4, title="安静分享", duration=6, purpose="沉淀情绪而不虚构功效",
             narrative_beat="游戏后的片刻分享", visual="儿童与白色毛绒熊并排坐在地毯上，窗光仍从相同方向落下。", camera="侧后方中景，缓慢轻推", action="儿童轻轻调整毛绒熊坐姿，带着自然笑意看向它。", voiceover="不用说什么，今天的小发现，也有了分享的对象。", on_screen_text="",
             continuity_anchor="保持地毯位置、窗光方向、毛绒熊面部和儿童右向视线。", transition="定格在完整可见的毛绒熊面部，环境声不断。", prompt="Quiet continuous live-action shot in the same living room. The same child gently adjusts the seated white plush bear beside them on the blue-and-white rug and smiles naturally. Preserve round ears, black round eyes, brown nose, wardrobe, sofa and daylight. No caption, logo or watermark.", model_hint="story"),
        Shot(id="shot-05", index=5, title="产品收束", duration=6, purpose="留出后期 CTA 的干净收束",
             narrative_beat="故事落在陪伴留在画面里", visual="白色毛绒熊在蓝白地毯上清晰可见，儿童安静坐在旁边，棕色沙发与自然窗光构成背景。", camera="竖版广中景，极轻微拉远", action="儿童与毛绒熊保持自然静止，给后期 Logo 与 CTA 留出上方净区。", voiceover="把这一刻，留在每一次回家的日常里。", on_screen_text="",
             continuity_anchor="最终帧必须完整露出白色毛绒熊圆耳、黑圆眼、棕鼻与坐姿；不生成任何品牌文字。", transition="以环境声和音乐尾音淡出；Logo 与 CTA 仅在 FFmpeg 后期叠加。", prompt="Closing live-action advertising frame in the supplied living room: the same white seated plush bear clearly visible on the blue-and-white rug with the same child seated nearby. Brown sofa and natural window light remain continuous. Clean upper negative space for postproduction only. No text, logo, watermark or product packaging.", model_hint="economy"),
    ]
    return CreativePlan(
        director_source="fallback", director_model="research-template-v1",
        director_note="素材事实优先的 30 秒研究模板；待人工批准 Brand Bible 后方可进入付费节点。",
        campaign_idea="以回家后的连续日常，介绍白色毛绒熊作为自然陪伴物。", logline="儿童回到熟悉客厅，发现白色毛绒熊，并在游戏与安静分享中让它留在日常画面里。",
        protagonist="素材中的儿童（不设姓名）", story_question="这段回家的日常会如何被一个安静的陪伴物串联？",
        hook="放学回家，熟悉的客厅里已有一个白色身影。", emotional_arc="发现 → 靠近 → 游戏 → 分享 → 留白收束",
        continuity_bible=["仅使用已批准素材事实", "全片同一客厅、同一服装、同一白色毛绒熊", "屏幕方向始终左到右", "Logo、CTA、字幕仅后期叠加", "禁止功效、安全、医疗与助眠承诺"],
        narration_script="一天结束，熟悉的客厅里，陪伴已经在等候。一个小小的目光相遇，让脚步慢了下来。游戏开始时，它只是安静地坐在身边。不用说什么，今天的小发现，也有了分享的对象。把这一刻，留在每一次回家的日常里。",
        visual_language="真实生活纪录片感；自然窗光；克制表演；竖版 9:16；不制作文字画面。", music_direction="轻盈木吉他与柔和室内环境声，镜头之间使用持续的房间底噪与动作声桥。",
        call_to_action="后期叠加：了解更多", sequences=[StorySequence(id="sequence-01", index=1, title="回家的陪伴", narrative_goal="完整呈现从回家到产品收束", turning_point="儿童注意到毛绒熊并靠近", continuity_scope="同一客厅、同一人物与同一白色毛绒熊", shot_ids=[shot.id for shot in shots], duration=30)], shots=shots,
    )
