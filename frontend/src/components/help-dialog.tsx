import { useState } from "react";
import { CircleHelp } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const FULL_STEPS = [
  {
    title: "新建采集轮次",
    body: "选关键词、页数和最多补全人数；运行结束后，达人会进入达人库等待审核。",
  },
  {
    title: "审核达人",
    body: "在达人库打开达人，设置采用状态。采用后会自动进入合作跟进（未联系），可继续更新合作状态。",
  },
  {
    title: "跟进合作",
    body: "在合作记录里维护状态、摘要、时间线、附件和作品；同一达人最多一个进行中的合作。",
  },
];

const LIMITED_STEPS = [
  {
    title: "浏览达人库",
    body: "用搜索和筛选找到达人，点开查看详情。",
  },
  {
    title: "审核标注",
    body: "设置采用状态、匹配方向、标签和备注；采用后自动进入合作跟进。",
  },
  {
    title: "跟进合作",
    body: "在合作记录里维护状态、摘要、时间线、附件和作品。",
  },
];

const FULL_GLOSSARY = [
  ["采集轮次", "一次搜索关键词得到的一批达人。同一轮次可以多次运行，每次运行叫一条“运行记录”。"],
  ["运行记录", "真正调用接口抓取的一次执行。重跑后统计以最新一次成功运行为准。"],
  ["采用状态", "按轮次记录的审核结果：待处理 / 备选 / 已采用 / 未采用。"],
  ["合作状态", "跨轮次共享的跟进主状态；采用后自动进入“未联系”，之后按合作推进。"],
  ["合作记录", "每次合作独立记录状态、摘要、附件和作品；用于保留历史证据链。"],
  ["线索", "待验证的外部名单。校验账号并“转为达人”后会进入达人库，审核状态为待处理。"],
  ["账号校验", "线索账号是否真实可用；与线索状态（待核实/核实中/已转为达人）是两个维度。"],
  ["资料补全", "是否抓取过粉丝、互动等主页资料。未补全的达人后续可再抓取。"],
  ["跟进人 / 负责人", "达人跟进人是责任标记，不限制他人编辑；线索负责人决定编辑、校验和转化权限。"],
];

const LIMITED_GLOSSARY = [
  ["采用状态", "对达人的审核结果：待处理 / 备选 / 已采用 / 未采用。"],
  ["合作状态", "达人主档上的跟进状态，不随筛选变化；采用后自动进入“未联系”。"],
  ["合作记录", "每次合作独立记录状态、摘要、附件和作品。"],
  ["跟进人", "达人的责任标记，不限制其他人查看和编辑。"],
  ["线索", "待验证的外部名单，与达人库分开管理。"],
  ["账号校验", "线索账号是否真实可用；与线索状态是两个维度。"],
  ["负责人", "线索的负责人，决定编辑和转化权限。"],
];

const FULL_RULES = [
  "采用达人后自动进入合作跟进；删除全部合作记录后，需要手动重新开始合作。",
  "重跑采集会用本次结果刷新统计：已审核/已认领达人保留，未审核且本次未命中的采集达人会从轮次移除。",
  "校验账号、以及勾选补全的线索转化会调用服务商接口并产生费用。",
];

const LIMITED_RULES = [
  "采用达人后自动进入合作跟进；删除全部合作记录后，需要手动重新开始合作。",
  "同一达人最多只有一个进行中的合作。",
  "线索负责人决定该线索的编辑和转化权限。",
];

export function HelpButton({ canCollect }: { canCollect: boolean }) {
  const [open, setOpen] = useState(false);
  const steps = canCollect ? FULL_STEPS : LIMITED_STEPS;
  const glossary = canCollect ? FULL_GLOSSARY : LIMITED_GLOSSARY;
  const rules = canCollect ? FULL_RULES : LIMITED_RULES;

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="快速上手"
            onClick={() => setOpen(true)}
          >
            <CircleHelp />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" align="end" sideOffset={6}>
          快速上手
        </TooltipContent>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>快速上手</DialogTitle>
            <DialogDescription>
              三分钟了解系统怎么用；完整说明见随项目提供的《使用手册》。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5">
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">主流程</h3>
              <ol className="space-y-2">
                {steps.map((step, index) => (
                  <li key={step.title} className="flex gap-3 text-sm">
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium">
                      {index + 1}
                    </span>
                    <span>
                      <span className="font-medium">{step.title}</span>
                      <span className="block text-muted-foreground">{step.body}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">常用词</h3>
              <dl className="space-y-2">
                {glossary.map(([term, meaning]) => (
                  <div key={term} className="grid gap-1 text-sm sm:grid-cols-[110px_1fr]">
                    <dt className="font-medium">{term}</dt>
                    <dd className="text-muted-foreground">{meaning}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">关键规则</h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {rules.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </section>

            <section className="space-y-1 border-t pt-4 text-sm text-muted-foreground">
              <p>需要开通更多功能或重置密码，请联系管理员。</p>
            </section>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
