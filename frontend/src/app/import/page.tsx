'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { useAccountsList } from '@/api/hooks/useAccounts';
import { useImportCsv } from '@/api/hooks/useImports';
import { ImportResult } from '@/api/generated/model';
import {
  UploadCloud,
  FileCheck,
  AlertCircle,
  CheckCircle2,
  Sliders,
  ExternalLink,
  Layers,
} from 'lucide-react';

export default function ImportPage() {
  const { data: accounts = [], isLoading: accountsLoading } = useAccountsList();
  const importMutation = useImportCsv();

  const [selectedAccountId, setSelectedAccountId] = useState<number | ''>('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [allowDuplicates, setAllowDuplicates] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setResult(null);

    if (!selectedAccountId) {
      setErrorMessage('Please select a target account.');
      return;
    }
    if (!selectedFile) {
      setErrorMessage('Please select a CSV file to upload.');
      return;
    }

    try {
      const res = await importMutation.mutateAsync({
        accountId: Number(selectedAccountId),
        file: selectedFile,
        allowDuplicates,
      });
      setResult(res);
      setSelectedFile(null);
    } catch (err: any) {
      setErrorMessage(err.response?.data?.detail || err.message || 'Import failed.');
    }
  };

  return (
    <PageLayout
      title="CSV Ingestion"
      description="Upload bank transactions and run classification against stored column mappings"
      breadcrumbs={[{ label: 'Import' }]}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl">
        {/* Upload Form */}
        <GlassCard>
          <h3 className="font-semibold text-foreground flex items-center gap-2 mb-3">
            <UploadCloud className="h-4 w-4 text-primary" />
            <span>Upload Bank Statement (CSV)</span>
          </h3>

          <form onSubmit={handleUpload} className="space-y-4">
            {errorMessage && (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-rose-300 flex items-center gap-2">
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            <div>
              <label className="text-xs font-medium text-foreground block mb-1">
                Target Account Profile *
              </label>
              <select
                value={selectedAccountId}
                onChange={(e) => setSelectedAccountId(e.target.value ? Number(e.target.value) : '')}
                className="w-full rounded-md border border-border bg-secondary/40 px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="">Select target account...</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} (•••• {a.last4}) - {a.account_kind}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs font-medium text-foreground block mb-1">
                CSV Statement File *
              </label>
              <input
                type="file"
                accept=".csv"
                onChange={handleFileChange}
                className="w-full text-xs text-muted-foreground file:mr-3 file:py-2 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-primary file:text-primary-foreground hover:file:bg-primary/90 cursor-pointer"
              />
              {selectedFile && (
                <p className="mt-1 text-xs text-foreground font-mono">
                  Selected: {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
                </p>
              )}
            </div>

            {/* allow_duplicates option */}
            <div className="rounded-md border border-border/60 bg-secondary/30 p-3">
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={allowDuplicates}
                  onChange={(e) => setAllowDuplicates(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-border text-primary"
                />
                <div>
                  <span className="text-xs font-semibold text-foreground">
                    Allow Duplicate Rows
                  </span>
                  <p className="mt-0.5 text-[11px] text-muted-foreground leading-normal">
                    Keeps same-identity rows via occurrence-suffixed deduplication hashes instead of skipping them.
                  </p>
                </div>
              </label>
            </div>

            <button
              type="submit"
              disabled={importMutation.isPending || !selectedFile || !selectedAccountId}
              className="w-full rounded-lg bg-primary py-2.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              <FileCheck className="h-4 w-4" />
              <span>{importMutation.isPending ? 'Processing Ingest...' : 'Import Statement'}</span>
            </button>
          </form>
        </GlassCard>

        {/* Import Results Card */}
        <GlassCard>
          <h3 className="font-semibold text-foreground flex items-center gap-2 mb-3">
            <Layers className="h-4 w-4 text-primary" />
            <span>Ingest Summary</span>
          </h3>

          {!result ? (
            <div className="flex flex-col items-center justify-center h-48 text-center text-xs text-muted-foreground p-4">
              <UploadCloud className="h-8 w-8 text-muted mb-2 opacity-50" />
              <span>Submit a CSV file to inspect row ingestion, deduplication, and classification stats.</span>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-xs font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-2.5 rounded-lg">
                <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                <span>
                  Import batch #{result.import_batch_id} recorded (Total rows read: {result.total_rows_read})
                </span>
              </div>

              {result.inserted === 0 && (
                <div className="rounded bg-amber-500/10 border border-amber-500/20 p-2 text-xs text-amber-300">
                  Notice: 0 new rows inserted (all were detected as existing duplicates). The batch row was still preserved.
                </div>
              )}

              {/* Statistics Grid */}
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="rounded-lg border border-border/40 bg-secondary/30 p-2.5">
                  <span className="text-muted-foreground">Inserted:</span>
                  <div className="font-mono text-lg font-bold text-foreground">{result.inserted}</div>
                </div>
                <div className="rounded-lg border border-border/40 bg-secondary/30 p-2.5">
                  <span className="text-muted-foreground">Duplicates Skipped:</span>
                  <div className="font-mono text-lg font-bold text-foreground">{result.duplicates_skipped}</div>
                </div>
              </div>

              {/* Unmapped Count Banner linking to Mappings Workbench */}
              <div className="rounded-lg border border-border/60 bg-secondary/40 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-foreground">
                    Unmapped Categories/Merchants
                  </span>
                  <Link
                    href="/mappings"
                    className="flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
                  >
                    <span>Rules Workbench</span>
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  New transactions may contain unmapped values. Visit the Rules Workbench to create mapping rules.
                </p>
              </div>

              {result.errors && result.errors.length > 0 && (
                <div className="rounded border border-destructive/40 bg-destructive/10 p-3 text-xs text-rose-300">
                  <span className="font-semibold block mb-1">Errors ({result.errors.length}):</span>
                  <ul className="list-disc pl-4 space-y-0.5 text-[11px]">
                    {result.errors.map((err, i) => (
                      <li key={i}>{typeof err === 'string' ? err : JSON.stringify(err)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </GlassCard>
      </div>
    </PageLayout>
  );
}
