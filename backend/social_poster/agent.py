"""The social_poster agent."""

from google.adk.agents import Agent

root_agent = Agent(
    name="social_poster",
    model="gemini-2.5-flash",
    description="Helps the user turn an idea into a polished social media post.",
    instruction="You help the user turn an idea into a social media post.",
)
