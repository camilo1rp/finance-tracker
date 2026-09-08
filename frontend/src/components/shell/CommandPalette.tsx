'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  LayoutDashboard,
  Receipt,
  CreditCard,
  UploadCloud,
  Sliders,
  FileBarChart,
  Bot,
  Settings,
  Search,
  RefreshCw,
  X,
} from 'lucide-react';
import { useReclassify } from '@/api/hooks/useTransactions';

export function CommandPalette({
  isOpen,
  onClose,
  onOpenAgent,
}: {
  isOpen: boolean;
  onClose: () => void;
  onOpenAgent: () => void;
}) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const reclassifyMutation = useReclassify();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        if (isOpen) {
          onClose();
        } else {
          // Open
          (window as any).__openCommandPalette?.();
        }
      }
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const actions = [
    { label: 'Go to Dashboard', icon: LayoutDashboard, run: () => router.push('/dashboard') },
    { label: 'Go to Transactions Ledger', icon: Receipt, run: () => router.push('/transactions') },
    { label: 'Go to Accounts & Profiles', icon: CreditCard, run: () => router.push('/accounts') },
    { label: 'Import Bank Statement (CSV)', icon: UploadCloud, run: () => router.push('/import') },
    { label: 'Go to Mappings & Rules Workbench', icon: Sliders, run: () => router.push('/mappings') },
    { label: 'Go to Analysis Artifacts', icon: FileBarChart, run: () => router.push('/artifacts') },
    {
      label: 'Open Agent Co-Pilot',
      icon: Bot,
      run: () => {
        onOpenAgent();
        onClose();
      },
    },
    {
      label: 'Run Full Ledger Reclassification',
      icon: RefreshCw,
      run: async () => {
        await reclassifyMutation.mutateAsync();
        alert('Ledger reclassification completed.');
        onClose();
      },
    },
  ];

  const filtered = actions.filter((a) =>
    a.label.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-20 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border/70 bg-card/95 shadow-2xl backdrop-blur-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center border-b border-border/40 px-4 py-3">
          <Search className="h-4 w-4 text-muted-foreground mr-2" />
          <input
            autoFocus
            type="text"
            placeholder="Type a command or search screens (⌘K)..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <button
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-72 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted-foreground">
              No matching commands.
            </div>
          ) : (
            filtered.map((action, i) => {
              const Icon = action.icon;
              return (
                <button
                  key={i}
                  onClick={() => {
                    action.run();
                    onClose();
                  }}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary/70 transition-colors"
                >
                  <Icon className="h-4 w-4 text-primary flex-shrink-0" />
                  <span>{action.label}</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
