'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard,
  Receipt,
  CreditCard,
  UploadCloud,
  Sliders,
  FileBarChart,
  Bot,
  Settings,
  ChevronLeft,
  ChevronRight,
  Wallet,
} from 'lucide-react';

export interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export const NAV_ITEMS = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/transactions', label: 'Transactions', icon: Receipt },
  { href: '/accounts', label: 'Accounts', icon: CreditCard },
  { href: '/import', label: 'CSV Import', icon: UploadCloud },
  { href: '/mappings', label: 'Mappings', icon: Sliders },
  { href: '/artifacts', label: 'Artifacts', icon: FileBarChart },
  { href: '/agent', label: 'Agent Co-Pilot', icon: Bot },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export function Sidebar({ collapsed, onToggleCollapse }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        'hidden md:flex flex-col border-r border-border/40 bg-card/60 backdrop-blur-xl transition-all duration-300 z-30',
        collapsed ? 'w-16' : 'w-56'
      )}
    >
      {/* Brand header */}
      <div className="flex h-16 items-center justify-between px-4 border-b border-border/40">
        <Link href="/dashboard" className="flex items-center gap-2.5 overflow-hidden">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground font-bold">
            <Wallet className="h-4 w-4" />
          </div>
          {!collapsed && (
            <span className="font-semibold tracking-tight text-foreground whitespace-nowrap text-sm">
              Finance Tracker
            </span>
          )}
        </Link>
        <button
          onClick={onToggleCollapse}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      {/* Nav items */}
      <nav className="flex-1 space-y-1.5 p-3">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors',
                isActive
                  ? 'bg-primary/15 text-primary border border-primary/20'
                  : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                collapsed && 'justify-center px-0'
              )}
              title={collapsed ? item.label : undefined}
            >
              <Icon className="h-4 w-4 flex-shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
