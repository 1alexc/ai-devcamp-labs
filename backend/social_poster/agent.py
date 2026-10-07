"""The social_poster agent."""

from google.adk.agents import Agent
from .tools import generate_image

root_agent = Agent(
    name="social_poster",
    model="gemini-2.5-flash",
    description="Helps the user turn an idea into a polished social media post.",
    instruction="""You help the user turn an idea into a social media post.
If the user asks for an image, call generate_image with a detailed visual
description and tell them where the file was saved.""",
    tools=[generate_image],
)
