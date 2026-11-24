import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "cn";

export type SearchableSelectOption = {
  value: string;
  label: string;
  description?: string;
  keywords?: string[];
  pinned?: boolean;
};

function matchesQuery(option: SearchableSelectOption, query: string) {
  return [option.label, option.description ?? "", ...(option.keywords ?? [])].some((text) =>
    text.toLowerCase().includes(query),
  );
}

export function SearchableSelect({
  label,
  options,
  value,
  onChange,
  placeholder = "请选择",
  searchPlaceholder = "搜索…",
  emptyText = "没有匹配项",
  className,
  disabled = false,
}: {
  label: string;
  options: SearchableSelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  className?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const listId = `${id}-listbox`;
  const optionId = (index: number) => `${id}-option-${index}`;
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const selected = options.find((item) => item.value === value) ?? null;
  const query = search.trim().toLowerCase();
  const matched = useMemo(
    () => (query ? options.filter((item) => matchesQuery(item, query)) : options),
    [options, query],
  );
  const filtered = useMemo(() => {
    if (!query) return options;
    const pinned = options.filter((item) => item.pinned && !matchesQuery(item, query));
    return [...matched, ...pinned];
  }, [options, query, matched]);
  const showEmpty = matched.length === 0;
  const active = filtered.length === 0 ? -1 : Math.min(activeIndex, filtered.length - 1);
  const activeOption = active >= 0 ? filtered[active] : undefined;

  useEffect(() => {
    if (!open || active < 0) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [active, open, filtered.length, query]);

  const select = (next: string) => {
    onChange(next);
    setOpen(false);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSearch("");
          const selectedIndex = options.findIndex((item) => item.value === value);
          setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            "h-8 w-fit justify-between gap-1.5 rounded-lg border-input bg-transparent px-2.5 py-2 pr-2 font-normal",
            className,
          )}
          aria-label={`${label}：${selected?.label ?? placeholder}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          title={
            selected
              ? `${selected.label}${selected.description ? ` · ${selected.description}` : ""}`
              : placeholder
          }
        >
          <span className={cn("truncate", !selected && "text-muted-foreground")}>
            {selected?.label ?? placeholder}
          </span>
          <ChevronDown className="size-4 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 gap-0 p-0">
        <div className="border-b p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-2 left-2 size-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => {
                const next = event.target.value;
                const nextQuery = next.trim().toLowerCase();
                setSearch(next);
                setActiveIndex(
                  nextQuery === "" || options.some((item) => matchesQuery(item, nextQuery)) ? 0 : -1,
                );
              }}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  if (filtered.length === 0) return;
                  setActiveIndex(active < 0 ? 0 : (active + 1) % filtered.length);
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  if (filtered.length === 0) return;
                  setActiveIndex(active <= 0 ? filtered.length - 1 : active - 1);
                } else if (event.key === "Enter") {
                  event.preventDefault();
                  if (activeOption) select(activeOption.value);
                }
              }}
              placeholder={searchPlaceholder}
              className="h-8 pl-8"
              aria-label={`搜索${label}`}
              role="combobox"
              aria-expanded
              aria-autocomplete="list"
              aria-controls={filtered.length > 0 ? listId : undefined}
              aria-activedescendant={activeOption ? optionId(active) : undefined}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        </div>
        <div ref={listRef} className="max-h-64 overflow-auto p-1">
          {showEmpty ? (
            <p className="px-2 py-2 text-center text-xs text-muted-foreground">{emptyText}</p>
          ) : null}
          {filtered.length > 0 ? (
            <div role="listbox" id={listId} aria-label={label}>
              {filtered.map((item, index) => (
                <button
                  key={item.value}
                  type="button"
                  id={optionId(index)}
                  role="option"
                  aria-selected={item.value === value}
                  data-index={index}
                  data-active={index === active}
                  className="relative flex w-full cursor-default items-center gap-1.5 rounded-md py-1 pr-8 pl-1.5 text-left text-sm outline-hidden select-none hover:bg-accent hover:text-accent-foreground data-[active=true]:bg-accent data-[active=true]:text-accent-foreground"
                  title={item.description ? `${item.label} · ${item.description}` : item.label}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => select(item.value)}
                >
                  <span className="min-w-0 truncate">{item.label}</span>
                  {item.description ? (
                    <span className="shrink-0 text-xs text-muted-foreground">{item.description}</span>
                  ) : null}
                  {item.value === value ? (
                    <span className="pointer-events-none absolute right-2 flex size-4 items-center justify-center">
                      <Check className="size-4" />
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
