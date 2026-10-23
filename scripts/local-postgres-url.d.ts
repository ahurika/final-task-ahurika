export class LocalPostgresUrlError extends Error {}

export function assertLocalPostgresUrl(value: string): string;

export function runAfterLocalPostgresUrlValidation<T>(
  value: string | undefined,
  action: (validatedUrl: string) => T,
): T;
