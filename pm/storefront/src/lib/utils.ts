import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Conditional + deduped className builder.
 * Used by shadcn-svelte components added later via `npx shadcn-svelte add`.
 */
export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}
