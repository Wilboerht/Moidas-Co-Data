export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_LENGTH = 200;

export function passwordLength(value: string): number {
  return Array.from(value).length;
}
