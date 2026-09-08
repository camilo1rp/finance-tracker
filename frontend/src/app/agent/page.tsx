'use client';

import React from 'react';
import { PageLayout } from '@/components/ui/PageLayout';
import { AgentChatPanel } from '@/components/agent/AgentChatPanel';

export default function AgentPage() {
  return (
    <PageLayout
      title="Agent Co-Pilot"
      description="Direct conversational view into the LangGraph coordinator"
      breadcrumbs={[{ label: 'Agent' }]}
    >
      <div className="h-[75vh] w-full max-w-4xl rounded-xl border border-border/60 overflow-hidden shadow-lg">
        <AgentChatPanel />
      </div>
    </PageLayout>
  );
}
