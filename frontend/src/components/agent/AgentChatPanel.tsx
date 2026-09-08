'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { API_BASE } from '@/api/custom-instance';
import { useAgentThreadState, useAgentChat, useAgentResume, useEmailSourceStatus } from '@/api/hooks/useAgent';
import { ArtifactRenderer } from '@/components/artifacts/registry';
import { StewardApprovalCard } from '@/components/artifacts/StewardApprovalCard';
import { Send, RefreshCw, PlusCircle, AlertCircle, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

const THREAD_STORAGE_KEY = 'ft_agent_thread_id';

function generateUuid(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function AgentChatPanel() {
  const router = useRouter();
  const [threadId, setThreadId] = useState<string>('');
  const [inputText, setInputText] = useState('');
  const [activeToolProgress, setActiveToolProgress] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Initialize or rehydrate thread_id from localStorage only (INV-09)
  useEffect(() => {
    let stored = localStorage.getItem(THREAD_STORAGE_KEY);
    if (!stored) {
      stored = generateUuid();
      localStorage.setItem(THREAD_STORAGE_KEY, stored);
    }
    setThreadId(stored);
  }, []);

  const { data: threadState, isLoading: stateLoading, refetch: refetchState } = useAgentThreadState(threadId);
  const { data: emailStatus } = useEmailSourceStatus();
  const chatMutation = useAgentChat();
  const resumeMutation = useAgentResume();

  const handleNewThread = () => {
    const newId = generateUuid();
    localStorage.setItem(THREAD_STORAGE_KEY, newId);
    setThreadId(newId);
  };

  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [threadState?.messages, activeToolProgress]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() || chatMutation.isPending || threadState?.interrupted) return;

    const message = inputText.trim();
    setInputText('');
    setActiveToolProgress('Starting co-pilot turn...');

    try {
      // Use SSE streaming transport if supported, fallback to mutation
      const response = await fetch(`${API_BASE}/agent/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, message }),
      });

      if (!response.ok || !response.body) {
        // Fallback to standard POST /agent/chat
        await chatMutation.mutateAsync({ thread_id: threadId, message });
        await refetchState();
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const evtStr of events) {
          const lines = evtStr.split('\n');
          let eventType = '';
          let eventData = '';

          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim();
            if (line.startsWith('data: ')) eventData = line.slice(6).trim();
          }

          if (eventType === 'tool') {
            try {
              const d = JSON.parse(eventData);
              setActiveToolProgress(`Delegating to ${d.subagent || d.tool}...`);
            } catch {}
          } else if (eventType === 'done' || eventType === 'interrupt') {
            await refetchState();
          }
        }
      }
    } catch {
      // Fallback invocation on stream drop
      await chatMutation.mutateAsync({ thread_id: threadId, message });
    } finally {
      setActiveToolProgress(null);
      await refetchState();
    }
  };

  const handleApprove = async (opsSubset: any[]) => {
    await resumeMutation.mutateAsync({
      thread_id: threadId,
      decision: 'approve',
      ops: opsSubset,
    });
    await refetchState();
  };

  const handleReject = async () => {
    await resumeMutation.mutateAsync({
      thread_id: threadId,
      decision: 'reject',
    });
    await refetchState();
  };

  const isPendingApproval = Boolean(threadState?.interrupted && threadState?.interrupt);

  return (
    <div className="flex h-full flex-col justify-between bg-card text-foreground">
      {/* Top action bar: thread id and new session */}
      <div className="flex items-center justify-between border-b border-border/40 px-4 py-2 text-[11px] text-muted-foreground bg-secondary/20">
        <span className="font-mono truncate max-w-[180px]">
          Thread: {threadId.slice(0, 8)}...
        </span>
        <button
          onClick={handleNewThread}
          className="flex items-center gap-1 hover:text-foreground transition-colors"
          title="Start fresh conversation"
        >
          <PlusCircle className="h-3.5 w-3.5" />
          <span>New Session</span>
        </button>
      </div>

      {/* Email provider notice if unavailable */}
      {emailStatus && !emailStatus.available && (
        <div className="flex items-center gap-2 border-b border-border/40 bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
          <AlertCircle className="h-3.5 w-3.5 text-amber-400 flex-shrink-0" />
          <span>
            EMAIL_PROVIDER is "{emailStatus.provider}". Enrichment reports unavailable out of the box.
          </span>
        </div>
      )}

      {/* Messages stream & GenUI Artifacts */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
        {stateLoading ? (
          <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            <span>Rehydrating thread state...</span>
          </div>
        ) : threadState?.messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-center text-xs text-muted-foreground p-6">
            <p className="font-medium text-foreground">AI Co-Pilot Ready</p>
            <p className="mt-1">
              Ask questions about spending trends, request email receipt enrichment, or review unmapped transactions.
            </p>
          </div>
        ) : (
          threadState?.messages.map((msg, idx) => {
            const isUser = msg.role === 'user';
            const isTool = msg.role === 'tool';
            if (isTool) return null; // Tool logs are internal, artifacts are rendered directly

            return (
              <div
                key={idx}
                className={cn('flex flex-col gap-1', isUser ? 'items-end' : 'items-start')}
              >
                <span className="text-[10px] text-muted-foreground capitalize">
                  {isUser ? 'You' : msg.name || 'Assistant'}
                </span>
                <div
                  className={cn(
                    'max-w-[85%] rounded-xl px-3.5 py-2.5 text-xs whitespace-pre-wrap',
                    isUser
                      ? 'bg-primary text-primary-foreground font-medium'
                      : 'bg-secondary/70 text-foreground border border-border/40'
                  )}
                >
                  {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}
                </div>
              </div>
            );
          })
        )}

        {/* Turn-produced GenUI Artifacts */}
        {threadState?.artifacts && threadState.artifacts.length > 0 && (
          <div className="space-y-3 pt-2">
            <span className="text-[10px] uppercase font-semibold tracking-wider text-muted-foreground">
              Generated Artifacts
            </span>
            {threadState.artifacts.map((art) => (
              <ArtifactRenderer
                key={art.artifact_id}
                artifact={{
                  id: art.artifact_id,
                  kind: art.kind,
                  title: art.title,
                  digest: art.digest,
                  spec: (art as any).spec,
                  data: (art as any).data,
                }}
                onOpen={(id) => router.push(`/artifacts/${id}`)}
              />
            ))}
          </div>
        )}

        {/* Pending Human Approval Gate */}
        {isPendingApproval && (
          <div className="pt-2">
            <StewardApprovalCard
              interrupt={threadState!.interrupt!}
              onApprove={handleApprove}
              onReject={handleReject}
              disabled={resumeMutation.isPending}
            />
          </div>
        )}

        {/* Tool execution progress indicator */}
        {activeToolProgress && (
          <div className="flex items-center gap-2 rounded-lg bg-secondary/50 p-2.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
            <span>{activeToolProgress}</span>
          </div>
        )}
      </div>

      {/* Input Box */}
      <form onSubmit={handleSendMessage} className="border-t border-border/40 p-3 bg-card/60">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={chatMutation.isPending || isPendingApproval}
            placeholder={
              isPendingApproval
                ? 'Approve or reject pending plan above...'
                : 'Ask the finance agent...'
            }
            className="flex-1 rounded-lg border border-border/70 bg-secondary/40 px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!inputText.trim() || chatMutation.isPending || isPendingApproval}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-opacity"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
      </form>
    </div>
  );
}
