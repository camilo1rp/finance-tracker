'use client';

import React from 'react';
import { usePathname } from 'next/navigation';
import { Bot, Search } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface TopBarProps {
  agentOpen: boolean;
  onToggleAgent: () => void;
}

export function TopBar({ agentOpen, onToggleAgent }: TopBarProps) {
  const pathname = usePathname();
  const pathSegments = pathname.split('/').filter(Boolean);

  return (
    <header className="flex h-16 items-center justify-between border-b border-border/40 bg-card/40 px-6 backdrop-blur-md z-20">
      {/* Breadcrumb path */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground capitalize">
        <span>App</span>
        {pathSegments.map((segment, idx) => (
          <React.Fragment key={idx}>
            <span>/</span>
            <span className={idx === pathSegments.length - 1 ? 'font-semibold text-foreground' : ''}>
              {segment}
            </span>
          </React.Fragment>
        ))}
      </div>

      {/* Action controls */}
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleAgent}
          className={cn(
            'flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-all shadow-sm',
            agentOpen
              ? 'border-primary bg-primary/20 text-primary'
              : 'border-border/60 bg-secondary/60 text-muted-foreground hover:bg-secondary hover:text-foreground'
          )}
        >
          <Bot className="h-4 w-4" />
          <span className="hidden sm:inline">Co-Pilot (30%)</span>
        </button>
      </div>
    </header>
  );
}
