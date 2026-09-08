'use client';

import React from 'react';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';

export default function SettingsPage() {
  return (
    <PageLayout
      title="Settings"
      description="System settings, ledger network parameters, and preferences"
      breadcrumbs={[{ label: 'Settings' }]}
    >
      <GlassCard className="max-w-2xl">
        <h3 className="font-semibold text-foreground">Single-User Local Deployment</h3>
        <p className="mt-2 text-xs text-muted-foreground leading-relaxed">
          The personal finance tracker is operating on a trusted local network. Authentication
          is deferred per architecture decision gate §13. Multi-user accounts are out of scope.
        </p>
      </GlassCard>
    </PageLayout>
  );
}
