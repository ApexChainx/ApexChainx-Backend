import React from "react";
/** ApexChain Network Operations Intelligence Platform */

interface KPICardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  highlight?: "green" | "red" | "blue" | "yellow";
}

const highlightMap: Record<string, string> = {
  green: "border-green-600 bg-green-50 dark:bg-green-900/30",
  red: "border-red-600 bg-red-50 dark:bg-red-900/30",
  blue: "border-blue-600 bg-blue-50 dark:bg-blue-900/30",
  yellow: "border-yellow-600 bg-yellow-50 dark:bg-yellow-900/30",
};

const valueColorMap: Record<string, string> = {
  green: "text-green-800 dark:text-green-400",
  red: "text-red-800 dark:text-red-400",
  blue: "text-blue-800 dark:text-blue-400",
  yellow: "text-yellow-800 dark:text-yellow-400",
};

const KPICard: React.FC<KPICardProps> = ({
  title,
  value,
  subtitle,
  highlight = "blue",
}) => {
  return (
    <div
      className={`rounded-xl border-l-4 p-5 shadow-sm ${highlightMap[highlight]}`}
      role="region"
      aria-label={title}
    >
      <p className="text-sm font-medium text-gray-600 dark:text-gray-300">{title}</p>
      <p className={`mt-1 text-3xl font-bold ${valueColorMap[highlight]}`}>
        {value}
      </p>
      {subtitle && <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">{subtitle}</p>}
    </div>
  );
};

export default KPICard;