# pipeline/
# Project-specific nightly pipelines.
# Each pipeline handles a project with a fixed schedule or domain-specific
# generation logic that doesn't fit the generic council → task queue flow.
#
# Current pipelines:
#   lang_pipeline.py  — 7-night Japanese/Spanish scene schedule for language-travel-app
#
# Future pipelines (as projects graduate to nightly runs):
#   meridian_pipeline.py
#   rts_pipeline.py
#   gamma_pipeline.py
#   ninja_pipeline.py
#   tax_pipeline.py
#
# All pipeline modules import from config.py in the orchestrator root.
# sys.path is set by orchestrator_main.py at startup — no manipulation needed here.
