'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard,
  Receipt,
  UploadCloud,
  Sliders,
  Bot,
} from 'lucide-react';

export interface BottomBarProps {
  onToggleAgent: () => void;
  agentOpen: boolean;
}

export function BottomBar({ onToggleAgent, agentOpen }: BottomBarProps) {
  const pathname = usePathname();

  const mobileLinks = [
    { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/transactions', label: 'Ledger', icon: Receipt },
    { href: '/import', label: 'Import', icon: UploadCloud },
    { href: '/mappings', label: 'Rules', icon: Sliders },
  ];

  return (
    <div className="md:hidden fixed bottom-0 left-0 right-0 z-40 border-t border-border/40 bg-card/90 backdrop-blur-lg px-2 py-1.5 flex items-center justify-around">
      {mobileLinks.slice(0, 2).map((link) => {
        const isActive = pathname === link.href;
        const Icon = link.icon;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={cn(
              'flex flex-col items-center gap-1 p-2 text-[10px] font-medium transition-colors',
              isActive ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Icon className="h-5 w-5" />
            <span>{link.label}</span>
          </Link>
        );
      })}

      {/* Center Agent Button */}
      <button
        onClick={onToggleAgent}
        className={cn(
          'flex flex-col items-center justify-center rounded-full p-2.5 -mt-4 shadow-lg transition-all',
          agentOpen
            ? 'bg-primary text-primary-foreground ring-4 ring-primary/20'
            : 'bg-secondary text-foreground border border-border'
        )}
        title="Toggle Agent Co-Pilot"
      >
        <Bot className="h-5 w-5" />
      </button>

      {mobileLinks.slice(2).map((link) => {
        const isActive = pathname === link.href;
        const Icon = link.icon;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={cn(
              'flex flex-col items-center gap-1 p-2 text-[10px] font-medium transition-colors',
              isActive ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Icon className="h-5 w-5" />
            <span>{link.label}</span>
          </Link>
        );
      })}
    </div>
  );
}
