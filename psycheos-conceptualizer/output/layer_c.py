"""Layer C Assembly - Metaphorical Narrative."""

from core.models import SessionState
from core.enums import PsycheLevelEnum
from .models import LayerC


def assemble_layer_c(session: SessionState) -> LayerC:
    """Assemble Layer C: Metaphorical Narrative."""
    
    # Determine dominant layer
    hypotheses = session.get_active_hypotheses()
    layer_counts = {}
    for hyp in hypotheses:
        for level in hyp.levels:
            layer_counts[level] = layer_counts.get(level, 0) + 1
    
    dominant = max(layer_counts.items(), key=lambda x: x[1])[0] if layer_counts else PsycheLevelEnum.L0
    
    # Select metaphor based on dominant layer
    metaphors = {
        PsycheLevelEnum.L0: ("Двигатель на пределе", 
                            "Вы как двигатель, который работает на максимальных оборотах без остановки. "
                            "Индикаторы показывают перегрев, но остановиться - значит не доехать. "
                            "Каждый день требует всё больше топлива при всё меньшей отдаче."),
        
        PsycheLevelEnum.L1: ("Охранник, который не может уйти",
                            "Представьте охранника, который стоит на посту годами, не позволяя себе расслабиться. "
                            "Даже когда опасность давно миновала, автоматизм не отключается. "
                            "Мышцы помнят напряжение лучше, чем разум помнит причину."),
        
        PsycheLevelEnum.L2: ("Жонглёр со слишком многими мячами",
                            "Вы жонглируете, и кто-то продолжает подбрасывать новые мячи. "
                            "Вы справляетесь, но каждое движение на пределе концентрации. "
                            "Уронить один - значит потерять контроль над всеми остальными."),
        
        PsycheLevelEnum.L3: ("Актёр в чужой роли",
                            "Вы играете роль, которую когда-то выбрали или вам назначили. "
                            "Реплики заучены, движения отработаны. Зрители аплодируют. "
                            "Но за кулисами вы больше не узнаёте себя в гриме."),
        
        PsycheLevelEnum.L4: ("Компас с неверным севером",
                            "Ваш внутренний компас указывает направление, по которому вы движетесь годами. "
                            "Но что если стрелка указывает туда, куда указывали другие, а не туда, куда зовёт вас? "
                            "Переориентация пугает - весь пройденный путь придётся пересмотреть."),
    }
    
    metaphor, narrative_base = metaphors.get(dominant, metaphors[PsycheLevelEnum.L0])
    
    # Direction of change
    directions = {
        PsycheLevelEnum.L0: "Научиться делать паузы. Не остановка навсегда, а передышка для ремонта.",
        PsycheLevelEnum.L1: "Обнаружить, что пост можно покинуть на несколько минут. Опасность не материализуется мгновенно.",
        PsycheLevelEnum.L2: "Позволить нескольким мячам упасть. Не всё требует немедленного внимания.",
        PsycheLevelEnum.L3: "Примерить другие роли. За кулисами. Без зрителей.",
        PsycheLevelEnum.L4: "Заметить разницу между 'должен' и 'хочу'. Хотя бы как гипотезу.",
    }
    
    direction = directions.get(dominant, directions[PsycheLevelEnum.L0])
    
    return LayerC(
        core_metaphor=metaphor,
        narrative=narrative_base,
        direction_of_change=direction
    )
