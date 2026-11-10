"""Report the executable Helios text parameters, not obsolete inherited @2 fields."""
from types import SimpleNamespace

def text_settings(model, arch=None):
    from .noise import evaluation
    from ..evalset.interp import Evaluator
    from ..evalset.syntax import parse
    ev=evaluation(model)
    # Parameters declared by this model, excluding included device census/metrics knobs.
    names=parse(ev.path.read_text(encoding='utf-8')).params
    context=SimpleNamespace(arch=arch if arch is not None else SimpleNamespace(device=None))
    evaluator=Evaluator(ev,context)
    return {'id':model.id,'table':ev.clock,
            **{name:evaluator.value(name) for name in names}}
