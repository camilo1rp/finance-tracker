'use client';

import React, { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { useTransaction, usePatchTransaction } from '@/api/hooks/useTransactions';
import { useOwnersList } from '@/api/hooks/useOwners';
import { useAccountsList } from '@/api/hooks/useAccounts';
import { formatMoney } from '@/lib/money';
import { TransactionPatch } from '@/api/generated/model';
import {
  ArrowLeft,
  CheckCircle,
  AlertCircle,
  Save,
  Layers,
  HelpCircle,
  ShieldAlert,
} from 'lucide-react';

export default function TransactionDetailPage() {
  const params = useParams();
  const router = useRouter();
  const transactionId = Number(params.id);

  const { data: transaction, isLoading, error } = useTransaction(transactionId);
  const { data: owners = [] } = useOwnersList();
  const { data: accounts = [] } = useAccountsList();
  const patchMutation = usePatchTransaction();

  // Form edit state (only dirty fields sent via model_fields_set)
  const [categoryOverride, setCategoryOverride] = useState<string>('');
  const [merchantOverride, setMerchantOverride] = useState<string>('');
  const [subcategory, setSubcategory] = useState<string>('');
  const [ownerId, setOwnerId] = useState<number | undefined>();
  const [typeOverride, setTypeOverride] = useState<string>('');
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Initial populate from transaction record
  useEffect(() => {
    if (transaction) {
      setCategoryOverride(transaction.category_override ?? '');
      setMerchantOverride(transaction.merchant_override ?? '');
      setSubcategory(transaction.subcategory ?? '');
      setOwnerId(transaction.owner_id ?? undefined);
      setTypeOverride(transaction.type_override ?? '');
    }
  }, [transaction]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!transaction) return;
    setSaveSuccess(false);

    // Build payload containing ONLY dirty fields
    const patchPayload: TransactionPatch = {};
    if (categoryOverride !== (transaction.category_override ?? '')) {
      patchPayload.category_override = categoryOverride.trim() || null;
    }
    if (merchantOverride !== (transaction.merchant_override ?? '')) {
      patchPayload.merchant_override = merchantOverride.trim() || null;
    }
    if (subcategory !== (transaction.subcategory ?? '')) {
      patchPayload.subcategory = subcategory.trim() || null;
    }
    if (ownerId !== (transaction.owner_id ?? undefined)) {
      patchPayload.owner_id = ownerId ?? null;
    }
    if (typeOverride !== (transaction.type_override ?? '')) {
      patchPayload.type_override = (typeOverride.trim() as any) || null;
    }

    if (Object.keys(patchPayload).length === 0) {
      return; // No dirty fields
    }

    await patchMutation.mutateAsync({
      transactionId,
      data: patchPayload,
    });
    setSaveSuccess(true);
  };

  if (isLoading) {
    return (
      <PageLayout title="Transaction Detail" breadcrumbs={[{ label: 'Transactions', href: '/transactions' }, { label: '...' }]}>
        <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
          Loading transaction record...
        </div>
      </PageLayout>
    );
  }

  if (error || !transaction) {
    return (
      <PageLayout title="Transaction Detail" breadcrumbs={[{ label: 'Transactions', href: '/transactions' }, { label: 'Error' }]}>
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-xs text-rose-300">
          Failed to load transaction #{transactionId}.
        </div>
      </PageLayout>
    );
  }

  const account = accounts.find((a) => a.id === transaction.account_id);

  return (
    <PageLayout
      title={`Transaction #${transaction.id}`}
      description={`Date: ${transaction.transaction_date} | Account: ${account?.name || transaction.account_id}`}
      breadcrumbs={[
        { label: 'Transactions', href: '/transactions' },
        { label: `#${transaction.id}` },
      ]}
      actions={
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 rounded-lg border border-border bg-secondary/50 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>Back to Ledger</span>
        </button>
      }
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 max-w-6xl">
        {/* Left 2 Cols: Classification Triples & Invariant Info */}
        <div className="lg:col-span-2 space-y-6">
          <GlassCard>
            <div className="flex items-center justify-between border-b border-border/40 pb-3 mb-4">
              <div>
                <span className="text-xs uppercase font-medium text-muted-foreground">Original Description</span>
                <h2 className="text-lg font-bold text-foreground mt-0.5">{transaction.description}</h2>
              </div>
              <div className="text-right">
                <span className="text-xs uppercase font-medium text-muted-foreground">Ledger Amount</span>
                <div className="text-xl font-mono font-bold text-foreground">{formatMoney(transaction.amount)}</div>
              </div>
            </div>

            {/* Triples Inspection Section */}
            <h3 className="font-semibold text-xs uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-1.5">
              <Layers className="h-4 w-4 text-primary" />
              <span>Classification Triples (Raw → Normalized → Override)</span>
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              {/* Category Triple */}
              <div className="rounded-lg border border-border/50 bg-secondary/30 p-3 space-y-2">
                <span className="font-semibold text-foreground block border-b border-border/30 pb-1">
                  Category Resolution
                </span>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">1. Raw (from CSV):</span>
                  <span className="font-mono text-foreground">{transaction.category_raw || '(none)'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">2. Normalized (from rule):</span>
                  <span className="font-mono text-foreground">{transaction.category_normalized || '(none)'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">3. Manual Override:</span>
                  <span className="font-mono text-foreground">{transaction.category_override || '(none)'}</span>
                </div>
                <div className="border-t border-border/30 pt-1.5 flex justify-between font-semibold">
                  <span className="text-primary">Effective Category:</span>
                  <span className="font-mono text-foreground">{transaction.effective_category || '(unassigned)'}</span>
                </div>
              </div>

              {/* Merchant Triple */}
              <div className="rounded-lg border border-border/50 bg-secondary/30 p-3 space-y-2">
                <span className="font-semibold text-foreground block border-b border-border/30 pb-1">
                  Merchant Resolution
                </span>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">1. Raw (from CSV):</span>
                  <span className="font-mono text-foreground">{transaction.merchant_raw || '(none)'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">2. Normalized (from rule):</span>
                  <span className="font-mono text-foreground">{transaction.merchant_normalized || '(none)'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">3. Manual Override:</span>
                  <span className="font-mono text-foreground">{transaction.merchant_override || '(none)'}</span>
                </div>
                <div className="border-t border-border/30 pt-1.5 flex justify-between font-semibold">
                  <span className="text-primary">Effective Merchant:</span>
                  <span className="font-mono text-foreground">{transaction.effective_merchant || '(none)'}</span>
                </div>
              </div>
            </div>

            {/* Additional Transaction Metadata */}
            <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 border-t border-border/30 pt-3 text-xs">
              <div>
                <span className="text-muted-foreground block">Raw Type:</span>
                <span className="font-mono text-foreground">{transaction.raw_type || '(none)'}</span>
              </div>
              <div>
                <span className="text-muted-foreground block">Effective Type:</span>
                <span className="font-mono text-foreground font-semibold">{transaction.effective_type}</span>
              </div>
              <div>
                <span className="text-muted-foreground block">Is Spend (INV-48):</span>
                <span className="font-mono font-semibold text-foreground">
                  {transaction.is_spend ? 'TRUE' : 'FALSE'}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground block">Raw Owner:</span>
                <span className="font-mono text-foreground">{transaction.owner_raw || '(none)'}</span>
              </div>
            </div>
          </GlassCard>

          {/* Invariant Guarantees Callout */}
          <div className="rounded-lg border border-border/60 bg-secondary/20 p-4 text-xs space-y-1.5 text-muted-foreground">
            <div className="flex items-center gap-1.5 font-semibold text-foreground">
              <ShieldAlert className="h-4 w-4 text-amber-400" />
              <span>Accounting Invariants Applied Here</span>
            </div>
            <p>
              • <strong>INV-19 (Overrides Survive Reclassify):</strong> Manual overrides entered here are preserved permanently through subsequent rule reclassifications.
            </p>
            <p>
              • <strong>Provenance Deletion:</strong> Any manual PATCH to category_override automatically deletes any matching transaction_overrides provenance row in the same transaction.
            </p>
            <p>
              • <strong>Subcategories:</strong> Subcategory is PATCH-only; bank statements never map subcategories at import.
            </p>
          </div>
        </div>

        {/* Right Col: Manual Override Edit Form */}
        <div>
          <GlassCard>
            <h3 className="font-semibold text-foreground mb-2">Manual Correction (PATCH)</h3>
            <p className="text-xs text-muted-foreground mb-4">
              Apply dirty-field corrections directly to this row.
            </p>

            <form onSubmit={handleSave} className="space-y-3">
              {saveSuccess && (
                <div className="rounded bg-emerald-500/10 border border-emerald-500/30 p-2 text-xs text-emerald-300 flex items-center gap-1.5">
                  <CheckCircle className="h-3.5 w-3.5" />
                  <span>Overrides updated successfully!</span>
                </div>
              )}

              <div>
                <label className="text-xs font-medium text-foreground">Category Override</label>
                <input
                  type="text"
                  placeholder="e.g. Dining, Travel, Groceries"
                  value={categoryOverride}
                  onChange={(e) => setCategoryOverride(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">Subcategory (PATCH-only)</label>
                <input
                  type="text"
                  placeholder="e.g. Coffee, Fast Food"
                  value={subcategory}
                  onChange={(e) => setSubcategory(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">Merchant Override</label>
                <input
                  type="text"
                  placeholder="Normalized merchant name"
                  value={merchantOverride}
                  onChange={(e) => setMerchantOverride(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">Owner Assignment</label>
                <select
                  value={ownerId ?? ''}
                  onChange={(e) => setOwnerId(e.target.value ? Number(e.target.value) : undefined)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">(None)</option>
                  {owners.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">Type Override (INV-48)</label>
                <select
                  value={typeOverride}
                  onChange={(e) => setTypeOverride(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">(None - keep original)</option>
                  <option value="SPEND">SPEND (Flips is_spend to true)</option>
                  <option value="INCOME">INCOME (Flips is_spend to false)</option>
                  <option value="TRANSFER">TRANSFER (Flips is_spend to false)</option>
                  <option value="REFUND">REFUND (Flips is_spend to false)</option>
                  <option value="PAYMENT">PAYMENT (Flips is_spend to false)</option>
                </select>
              </div>

              <button
                type="submit"
                disabled={patchMutation.isPending}
                className="w-full mt-4 flex items-center justify-center gap-2 rounded-lg bg-primary py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <Save className="h-4 w-4" />
                <span>{patchMutation.isPending ? 'Saving...' : 'Save Changes'}</span>
              </button>
            </form>
          </GlassCard>
        </div>
      </div>
    </PageLayout>
  );
}
