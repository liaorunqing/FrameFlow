from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.main import store, workflow_store
from backend.app.orchestration import build_workflow
from backend.app.production import build_production_plan
from backend.app.schemas import CreativePlan, Project, Shot, StorySequence


PROJECT_ID = "afa97e5b-1325-4c8d-96ba-28cbb99b04c6"


def shot(index: int, title: str, purpose: str, beat: str, visual: str, camera: str,
         action: str, voiceover: str, transition: str, prompt: str) -> Shot:
    return Shot(
        id=str(uuid4()), index=index, title=title, duration=6, purpose=purpose,
        narrative_beat=beat, visual=visual, camera=camera, action=action,
        voiceover=voiceover, on_screen_text="", transition=transition,
        continuity_anchor=(
            "同一位约8岁黑色短发女孩，黄色针织开衫、白色上衣和海军蓝书包；"
            "同一只紫粉蓝渐变毛绒语小贝，紫色耳朵与四肢、黑色屏幕脸、"
            "蓝色圆眼、粉色挂绳；同一间木桌、米色沙发与左侧窗光的客厅。"
        ),
        prompt=prompt,
        model_hint="story" if index < 5 else "economy",
    )


def main() -> None:
    project = Project.model_validate(store.get_project(PROJECT_ID))
    common = (
        " Photorealistic live-action family commercial, vertical 9:16, restrained natural acting, "
        "soft daylight from screen-left, physically plausible hands. The product must remain the exact "
        "same small round fluffy AI learning toy: pastel lavender-pink-light-blue gradient long fur, "
        "two purple rounded ears, two short purple arms and feet, glossy black rectangular screen face "
        "with two bright blue circular eyes and a tiny smile, pink wrist strap. Preserve size and geometry. "
        "No generated words on the screen, no subtitles, logo, watermark, extra limbs, or redesign."
    )
    shots = [
        shot(1, "把不会说的话带回家", "前三秒建立人物困境并让商品自然出现",
             "女孩放学回家，从书包侧边取下随身携带的语小贝。",
             "玄关通向温暖客厅，女孩推门进来，语小贝通过粉色挂绳挂在海军蓝书包侧面。",
             "35mm肩部高度中景，跟随女孩从玄关进入，最后轻缓推近挂绳上的产品。",
             "女孩放下书包，看到英语练习卡，短暂停顿后把语小贝从挂绳上取下放到木桌上。",
             "今天有一句英语，她想了很久，还是没能说出口。",
             "书包落桌的轻响作为声音桥；顺着她的视线切到桌面。",
             "A school-age girl enters a warm living room after school. The exact toy hangs from the side of her navy backpack by its pink strap. She sets the backpack beside a wooden table, notices a plain study card, pauses, then gently removes the toy and places it upright on the table." + common),
        shot(2, "先问一句", "以生活动作展示智能对话入口",
             "女孩把不会表达的意思说给语小贝听，产品屏幕以表情回应。",
             "同一木桌边，女孩坐下，语小贝位于她右手前方；屏幕只有蓝色眼睛和简单表情。",
             "50mm双人近景般的桌面构图，先看犹豫的手指，再缓慢移焦到屏幕表情。",
             "女孩轻触屏幕下方的实体触控区，低声说出半句；产品蓝眼睛轻微眨动，她安静听完。",
             "她试着问：这句话，用英语应该怎么说？",
             "产品开始回应的柔和声音提前半秒进入下一镜，形成声音桥。",
             "At the same table, the girl sits with the exact toy upright near her right hand. She gently taps the physical touch area below its face and speaks a short question. The screen keeps only two blue animated eyes and a small smile, blinking subtly as if listening. She leans closer and waits, without exaggerated reaction." + common),
        shot(3, "听懂，再跟读", "清楚展示英语口语练习而非抽象陪伴",
             "产品给出简短示范，女孩先听再自然跟读一次。",
             "同一桌面和左侧窗光，语小贝完整外观处于前景，女孩位于后景侧面。",
             "65mm产品近景，极慢侧向移动；一次自然转焦从蓝色眼睛到女孩嘴形。",
             "蓝色眼睛随语音节奏轻微变化；女孩认真听，停顿一下，然后用自然口型跟读一句英语。",
             "先听一遍，再慢慢说一遍。原来那句话没有那么难。",
             "女孩说完最后一个词时抬眼，动作匹配切到她拿起练习卡。",
             "Close product-focused shot at the same table. The exact toy's two blue eyes change expression subtly with a calm spoken response, without any text. Rack focus once from the toy to the girl's natural mouth movement as she listens, pauses, and repeats one short English sentence. Keep the full toy silhouette readable." + common),
        shot(4, "换一个词，也能明白", "以一次具体查询带出翻译与词典功能",
             "女孩遇到另一个词，向语小贝查询词义后在练习卡旁做出理解动作。",
             "同一客厅木桌，练习卡不含可辨文字；产品位于女孩与卡片之间。",
             "45mm俯侧中近景，沿女孩手指从卡片移动到产品，镜头不过轴。",
             "女孩指向卡片上的一个模糊词位，再问一句；听到解释后轻轻点头，把卡片翻到下一面。",
             "遇到不懂的词，也可以马上问清楚。",
             "翻卡动作在切点前开始，下一镜沿同方向完成，形成动作匹配。",
             "In the same living room, the girl points to one intentionally unreadable word area on a study card, then asks the exact toy a brief question. The toy responds through subtle blue-eye animation only. She understands, gives one small nod, and begins turning the card over. No readable card text." + common),
        shot(5, "把这句话说给家人听", "用人物变化收束故事并最后落产品",
             "女孩把刚练习的英语自然说给画外家人听，产品成为变化的见证而非悬浮展品。",
             "同一客厅，女孩站在桌旁面对画外家人；语小贝完整坐在木桌前景，夕阳略暖。",
             "35mm中景从女孩完成翻卡动作开始，轻缓后退，最后转焦到桌面产品并保持一秒。",
             "女孩抬头向画外家人自然说出一句英语，听到回应后露出克制微笑；最后一秒产品静稳清晰。",
             "会说出来的那一刻，练习就真正走进了生活。",
             "环境声保留半秒，随后由后期叠加产品名与行动提示。",
             "The card-turn action completes in the same direction. The girl looks toward a family member who stays fully off screen and naturally says one short English sentence. After hearing a warm off-screen response, she gives a restrained smile. The camera eases back and racks focus to the exact toy sitting upright in the foreground, holding a clean one-second product ending." + common),
    ]
    sequences = [
        StorySequence(id=str(uuid4()), index=1, title="问题出现", narrative_goal="让观众先理解孩子不会表达的具体困境", turning_point="她决定开口询问", continuity_scope="玄关进入客厅；书包与产品挂绳完成动作交接", shot_ids=[shots[0].id, shots[1].id], duration=12),
        StorySequence(id=str(uuid4()), index=2, title="听懂并练习", narrative_goal="以听、说、查三个动作证明功能", turning_point="她第一次完整跟读", continuity_scope="木桌、左侧窗光、人物服装与产品外观固定", shot_ids=[shots[2].id, shots[3].id], duration=12),
        StorySequence(id=str(uuid4()), index=3, title="变化被听见", narrative_goal="让学习结果进入真实家庭交流", turning_point="她主动向家人说出英语", continuity_scope="翻卡动作匹配，画外家人不入镜，最后产品定格", shot_ids=[shots[4].id], duration=6),
    ]
    plan = CreativePlan(
        director_source="fallback", director_model="shotcraft-curated-v1",
        director_note="自主自由创作；依据用户商品图与功能资料人工事实校正，禁止模型生成文字与医疗承诺。",
        campaign_idea="一句没说出口的英语，在一次自然询问后成为能分享给家人的话。",
        logline="女孩把不会表达的一句话带回家，通过语小贝的对话、跟读和词义查询，最终自然地说给家人听。",
        protagonist="一位约8岁、放学回家的女孩；自然克制，不对镜头表演。",
        story_question="她能否把今天没说出口的英语，真正说给家人听？",
        hook="书包放下后，她面对一张练习卡停住了。",
        emotional_arc="犹豫 → 开口询问 → 听懂跟读 → 主动表达",
        continuity_bible=[
            "女孩始终为黑色短发、黄色针织开衫、白色上衣、海军蓝书包。",
            "语小贝始终为紫粉蓝渐变长绒、紫色耳朵四肢、黑色屏幕脸、蓝色圆眼和粉色挂绳。",
            "客厅始终为木桌、米色沙发、左侧自然窗光；镜头不跨越屏幕方向。",
            "生成画面不制作文字、Logo、字幕或功能标签，全部由后期叠加。",
            "不出现心理、治疗、私教、成长保证和无法验证的语言覆盖承诺。",
        ],
        narration_script=" ".join(item.voiceover for item in shots),
        visual_language="真实家庭电影感；暖灰背景承托产品的紫粉蓝；单一主动作、克制运镜、动作匹配与声音桥。",
        music_direction="轻柔木琴与原声拨弦，84 BPM 左右；旁白与产品回应时主动让出频段。",
        call_to_action="把今天想说的话，慢慢说出来。",
        sequences=sequences, shots=shots,
    )
    updated = project.model_copy(update={"creative_plan": plan, "status": "planned", "updated_at": datetime.now()})
    store.put_project(updated.model_dump(mode="json"))
    workflow_store.put(build_workflow(updated, build_production_plan(updated)))
    print(PROJECT_ID)


if __name__ == "__main__":
    main()
