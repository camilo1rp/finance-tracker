import React from 'react';
import { cn } from '@/lib/utils';

export interface PageLayoutProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  breadcrumbs?: { label: string; href?: string }[];
  children: React.ReactNode;
  className?: string;
}

export function PageLayout({
  title,
  description,
  actions,
  breadcrumbs,
  children,
  className,
}: PageLayoutProps) {
  return (
    <div className={cn('flex flex-col gap-6 p-6 md:p-8', className)}>
      {/* Header section */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          {breadcrumbs && breadcrumbs.length > 0 && (
            <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              {breadcrumbs.map((b, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <span>/</span>}
                  {b.href ? (
                    <a href={b.href} className="hover:text-foreground">
                      {b.label}
                    </a>
                  ) : (
                    <span>{b.label}</span>
                  )}
                </React.Fragment>
              ))}
            </div>
          )}
          <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
            {title}
          </h1>
          {description && (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-3">{actions}</div>}
      </div>

      {/* Main Content Area */}
      <div className="flex-1">{children}</div>
    </div>
  );
}
