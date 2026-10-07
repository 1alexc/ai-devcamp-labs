"""The social_poster agent."""

import pathlib

from google.adk.agents import Agent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.skill_toolset import SkillToolset

from .tools import generate_image

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

skill_toolset = SkillToolset(
    skills=[
        load_skill_from_dir(SKILLS_DIR / "post-formatter"),
        load_skill_from_dir(SKILLS_DIR / "platform-style"),
        load_skill_from_dir(SKILLS_DIR / "brand-voice"),
        load_skill_from_dir(SKILLS_DIR / "poster-style"),
    ],
)

search_agent = Agent(
    name="web_researcher",
    model="gemini-2.5-flash",
    description="Researches facts, dates, and context on the web.",
    instruction="Research the given query and return a concise summary of relevant facts.",
    tools=[google_search],
)

root_agent = Agent(
    name="social_poster",
    model="gemini-2.5-flash",
    description="Helps the user turn an idea into a polished social media post.",
    instruction="""You help the user turn an idea into a social media post.

Workflow:
1. If the idea needs facts, dates, or context, research it with the
   web_researcher tool first.
2. When drafting, load and follow the relevant skills: post-formatter
   (structure), platform-style (rules for the target platform), and
   brand-voice (tone).
3. If the user asks for an image, load and follow the poster-style skill,
   then call generate_image with a detailed visual description and tell
   them where the file was saved.""",
    tools=[AgentTool(agent=search_agent), generate_image, skill_toolset],
)
