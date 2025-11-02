# orchestrator/__init__.py
__all__ = ["root_agent"]
from .agent_orchestrator import orchestrator_agent as root_agent


# import os
# try:
#     from dotenv import load_dotenv
#     _here = os.path.dirname(os.path.abspath(__file__))
#     _root = os.path.abspath(os.path.join(_here, os.pardir))
#     load_dotenv(os.path.join(_root, ".env"))
# except Exception:
#     pass

# from .agent_orchestrator import orchestrator_agent as root_agent

# __all__ = ["root_agent"]  # only this shows up as “the” agent
