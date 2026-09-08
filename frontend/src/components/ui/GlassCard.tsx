import React from 'react';
import { cn } from '@/lib/utils';

export interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'accent' | 'warning' | 'subtle';
  interactive?: boolean;
}

export function GlassCard({
  children,
  className,
  variant = 'default',
  interactive = false,
  ...props
}: GlassCardProps) {
  const variantStyles = {
    default: 'bg-card/70 border-border/60 text-card-foreground backdrop-blur-md',
    accent: 'bg-primary/10 border-primary/30 text-foreground backdrop-blur-md',
    warning: 'bg-amber-950/20 border-amber-600/30 text-amber-200 backdrop-blur-md',
    subtle: 'bg-secondary/40 border-border/40 text-secondary-foreground backdrop-blur-sm',
  };

  return (
    <div
      className={cn(
        'rounded-xl border p-5 shadow-sm transition-all duration-200',
        variantStyles[variant],
        interactive && 'hover:border-primary/50 hover:shadow-md cursor-pointer hover:bg-card/90',
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
