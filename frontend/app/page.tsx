"use client";

import { useState } from "react";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ProfilePanel } from "@/components/profile/ProfilePanel";
import { ResourceTray } from "@/components/resources/ResourceTray";
import { Sidebar } from "@/components/layout/Sidebar";

export default function HomePage() {
  const [sessionId, setSessionId] = useState<string>(
    () => crypto.randomUUID()
  );
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        sessionId={sessionId}
        onNewSession={() => setSessionId(crypto.randomUUID())}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
      />

      <main className="flex-1 flex flex-col bg-bg min-w-0">
        <header className="border-b border-fg/10 px-6 py-3 flex items-center justify-between bg-bg-panel/50 backdrop-blur">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-accent flex items-center justify-center text-white font-bold">
              T
            </div>
            <div>
              <h1 className="font-semibold">Tutor</h1>
              <p className="text-xs text-fg-muted">
                多智能体个性化学习资源生成系统
              </p>
            </div>
          </div>
          <div className="text-xs text-fg-muted">
            Session: <code className="text-accent">{sessionId.slice(0, 8)}</code>
          </div>
        </header>

        <div className="flex-1 flex overflow-hidden">
          <section className="flex-1 flex flex-col min-w-0">
            <ChatMessages sessionId={sessionId} />
            <ChatComposer sessionId={sessionId} />
          </section>

          <aside className="w-96 border-l border-fg/10 bg-bg-panel overflow-y-auto">
            <ProfilePanel />
            <ResourceTray />
          </aside>
        </div>
      </main>
    </div>
  );
}
