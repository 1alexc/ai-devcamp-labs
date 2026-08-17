"use client";

// Approved posts + generated images, pulled from the same AG-UI message
// stream ActivityFeed reads. Images are local files written by
// tools.py:generate_image — backend/main.py mounts them at /outputs so the
// browser can actually render them.

import { useCopilotChatInternal } from "@copilotkit/react-core";

const BACKEND_ORIGIN = process.env.NEXT_PUBLIC_BACKEND_ORIGIN || "http://localhost:8000";

type PostEntry = {
  id: string;
  text: string;
  platform: string;
  url?: string;
  failed: boolean;
};

type ImageEntry = {
  id: string;
  url: string;
  prompt: string;
};

function tryParseJSON(raw: unknown): any {
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

// MCP tool results are sometimes wrapped as {content: [{type:"text", text: "<json>"}]}
// (Buffer's remote server) vs. a plain dict (LinkedIn's local FastMCP server).
function unwrapMcpContent(result: any): any {
  if (result && Array.isArray(result.content) && typeof result.content[0]?.text === "string") {
    return tryParseJSON(result.content[0].text);
  }
  return result;
}

// Buffer's create_post takes a channelId; LinkedIn's (phase 2 MCP) does not.
function inferPlatform(args: any): string {
  return args?.channelId ? "Buffer" : "LinkedIn";
}

function collectGallery(messages: any[]): { images: ImageEntry[]; posts: PostEntry[] } {
  const images: ImageEntry[] = [];
  const posts: PostEntry[] = [];
  const pending = new Map<string, { name: string; args: any }>();

  for (const msg of messages) {
    if (msg.role === "assistant" && Array.isArray(msg.toolCalls)) {
      for (const call of msg.toolCalls) {
        const name = call.function?.name;
        if (name === "generate_image" || name === "create_post") {
          pending.set(call.id, { name, args: tryParseJSON(call.function?.arguments) });
        }
      }
    } else if (msg.role === "tool" && msg.toolCallId) {
      const call = pending.get(msg.toolCallId);
      if (!call) continue;
      const result = unwrapMcpContent(tryParseJSON(msg.content));
      const failed = Boolean(msg.error) || result?.isError === true || result?.status === "error";

      if (call.name === "generate_image") {
        if (!failed && result?.image_path) {
          const filename = String(result.image_path).split("/").pop();
          images.push({
            id: msg.toolCallId,
            url: `${BACKEND_ORIGIN}/outputs/${filename}`,
            prompt: call.args?.prompt ?? "",
          });
        }
      } else if (call.name === "create_post" && !failed) {
        posts.push({
          id: msg.toolCallId,
          text: call.args?.text ?? "",
          platform: inferPlatform(call.args),
          url: result?.post_url ?? result?.url,
          failed: false,
        });
      }
    }
  }
  return { images, posts };
}

export function PostGallery() {
  const { messages } = useCopilotChatInternal();
  const { images, posts } = collectGallery((messages as any[]) ?? []);

  if (images.length === 0 && posts.length === 0) {
    return null; // nothing approved/generated yet — keep the dashboard uncluttered
  }

  return (
    <section className="card">
      <h2>Approved Posts &amp; Images</h2>
      {posts.length > 0 && (
        <ul className="gallery-posts">
          {posts.map((p) => (
            <li key={p.id}>
              <span className="gallery-platform">{p.platform}</span>
              <p>{p.text.length > 160 ? `${p.text.slice(0, 160)}…` : p.text}</p>
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer">
                  view
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {images.length > 0 && (
        <div className="gallery-images">
          {images.map((img) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={img.id} src={img.url} alt={img.prompt} title={img.prompt} />
          ))}
        </div>
      )}
    </section>
  );
}
