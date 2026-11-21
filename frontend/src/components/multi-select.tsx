import { useId, useMemo, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "cn";

export type MultiSelectOption = {
  value: string;
  label: string;
};

export function MultiSelect({
  label,
  options,
  value,
  onChange,
  placeholder,
  searchPlaceholder = "搜索…",
  emptyText = "暂无选项",
  className,
  disabled = false,
}: {
  label: string;
  options: MultiSelectOption[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyText?: string;
  className?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selected = new Set(value);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return options;
    return options.filter((item) => item.label.toLowerCase().includes(query));
  }, [options, search]);
  const selectedLabels = options.filter((item) => selected.has(item.value)).map((item) => item.label);
  const summary =
    selectedLabels.length === 0
      ? placeholder
      : selectedLabels.length <= 2
        ? selectedLabels.join("、")
        : `已选 ${selectedLabels.length} 项`;

  const toggle = (item: string, checked: boolean) => {
    const next = new Set(value);
    if (checked) next.add(item);
    else next.delete(item);
    onChange([...next]);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn("w-full justify-between font-normal", className)}
          aria-label={label}
        >
          <span className={cn("truncate", selectedLabels.length === 0 && "text-muted-foreground")}>
            {summary}
          </span>
          <ChevronDown className="size-4 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 gap-0 p-0">
        <div className="space-y-2 border-b p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-2 left-2 size-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={searchPlaceholder}
              className="h-8 pl-8"
              aria-label={`搜索${label}`}
            />
          </div>
          <div className="flex items-center justify-between">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => onChange(options.map((item) => item.value))}
            >
              全选
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => onChange([])}
            >
              清除
            </Button>
          </div>
        </div>
        <div className="max-h-64 overflow-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-4 text-center text-sm text-muted-foreground">{emptyText}</p>
          ) : (
            filtered.map((item) => {
              const itemId = `${id}-${item.value}`;
              return (
                <label
                  key={item.value}
                  htmlFor={itemId}
                  className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-sm hover:bg-muted"
                >
                  <Checkbox
                    id={itemId}
                    checked={selected.has(item.value)}
                    onCheckedChange={(checked) => toggle(item.value, checked === true)}
                  />
                  <span className="min-w-0 truncate">{item.label}</span>
                </label>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
