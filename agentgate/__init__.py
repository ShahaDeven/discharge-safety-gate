"""agentgate: replay conversations through any call agent, score them, and gate changes.

The core never imports a company layer (discharge/ here); a company layer supplies the
scenarios, the context renderer and the scoring rules.
"""
