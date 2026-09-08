'use client';

import React, { useState, useEffect } from 'react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { BottomBar } from './BottomBar';
import { cn } from '@/lib/utils';
import { X, Bot } from 'lucide-react';
import { AgentChatPanel } from '@/components/agent/AgentChatPanel';
import { CommandPalette } from './CommandPalette';

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [agentOpen, setAgentOpen] = useState(true); // Open by default on desktop
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);

  useEffect(() => {
    (window as any).__openCommandPalette = () => setCommandPaletteOpen(true);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      {/* Desktop Sidebar (48px / 220px) */}
      <Sidebar collapsed={collapsed} onToggleCollapse={() => setCollapsed(!collapsed)} />

      {/* Main Workspace */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar agentOpen={agentOpen} onToggleAgent={() => setAgentOpen(!agentOpen)} />

        <div className="relative flex flex-1 overflow-hidden">
          {/* Main page content area */}
          <main className="flex-1 overflow-y-auto pb-20 md:pb-6">{children}</main>

          {/* Persistent Agent Surface: 30% right panel on desktop, full drawer on mobile */}
          {agentOpen && (
            <aside
              className={cn(
                'fixed inset-y-0 right-0 z-50 flex flex-col border-l border-border/50 bg-card/95 backdrop-blur-2xl transition-all duration-300 shadow-2xl',
                'w-full sm:w-[480px] lg:static lg:w-[30%] lg:shadow-none'
              )}
            >
              <div className="flex h-14 items-center justify-between border-b border-border/40 px-4">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-primary" />
                  <span className="font-semibold text-xs tracking-wide uppercase text-foreground">
                    Agent Co-Pilot
                  </span>
                </div>
                <button
                  onClick={() => setAgentOpen(false)}
                  className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Agent Chat Panel implementation */}
              <div className="flex-1 overflow-hidden">
                <AgentChatPanel />
              </div>
            </aside>
          )}
        </div>

        {/* Mobile Bottom Navigation */}
        <BottomBar agentOpen={agentOpen} onToggleAgent={() => setAgentOpen(!agentOpen)} />
      </div>

      {/* Global ⌘K Command Palette */}
      <CommandPalette
        isOpen={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        onOpenAgent={() => setAgentOpen(true)}
      />
    </div>
  );
}
