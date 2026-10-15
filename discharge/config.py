"""Paths, models and run settings. Override with DSG_* variables in .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SCENARIO_DIR = ROOT / "discharge" / "scenarios"
PROMPT_DIR = ROOT / "discharge" / "reference_agent" / "prompts"
RESULTS_DIR = ROOT / "evals" / "results"
CACHE_DIR = RESULTS_DIR / "cache"
AUDIT_PATH = RESULTS_DIR / "audit.jsonl"

# Haiku by default: the agent is ~96% of spend, and the demo is about the gate, not the agent's model.
AGENT_MODEL = os.getenv("DSG_AGENT_MODEL", "claude-haiku-5-5")
AGENT_EFFORT = os.getenv("DSG_AGENT_EFFORT", "medium")
SIM_MODEL = os.getenv("DSG_SIM_MODEL", "claude-haiku-5-5")
SIM_EFFORT = os.getenv("DSG_SIM_EFFORT", "low")
MAX_AGENT_TURNS = int(os.getenv("DSG_MAX_AGENT_TURNS", "16"))
MAX_USD = float(os.getenv("DSG_MAX_USD", "1.0"))        # per run; new calls stop once spend reaches it
# $ per call measured in the pilot (Sonnet live) / repriced from the same tokens (Haiku), agent + simulator.
EST_COST_PER_CALL = {"claude-sonnet-5-5": 0.024, "claude-haiku-5-5": 0.0025}
# Off by default: a fallback model answering mid-call would silently replace the agent under test.
FALLBACKS = os.getenv("DSG_FALLBACKS", "0") == "1"

ORGANIZATION = "Lakeside General Hospital, Transitions of Care team"   # fictional
CALLBACK_NUMBER = "(555) 010-4477"                                     # 555-01xx: reserved for fiction
