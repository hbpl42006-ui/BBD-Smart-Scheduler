#!/bin/bash

# Define pages
declare -a PAGES=(
  "dashboard"
  "academic-setup/sessions"
  "academic-setup/semesters"
  "academic-setup/programs"
  "academic-setup/sections"
  "courses"
  "faculty"
  "rooms-labs"
  "time-slots"
  "timetables"
  "room-allocation"
  "notifications"
  "reports"
)

mkdir -p src/app

for PAGE in "${PAGES[@]}"; do
  mkdir -p "src/app/$PAGE"
  
  # Format title: e.g., "academic-setup/sessions" -> "Sessions", "dashboard" -> "Dashboard", "rooms-labs" -> "Rooms Labs"
  TITLE=$(basename "$PAGE" | sed -e 's/-/ /g' | awk '{for(i=1;i<=NF;i++)sub(/./,toupper(substr($i,1,1)),$i)}1')
  
  cat <<EOF > "src/app/$PAGE/page.tsx"
import { AdminLayout } from '@/components/layout/AdminLayout';
import { Header } from '@/components/layout/Header';

export default function ${TITLE// /}Page() {
  return (
    <AdminLayout>
      <Header title="$TITLE" />
      <div className="p-6">
        <h2 className="text-2xl font-bold tracking-tight">Welcome to $TITLE</h2>
        <p className="text-muted-foreground mt-2">
          Placeholder for $TITLE functionality.
        </p>
      </div>
    </AdminLayout>
  );
}
EOF
done
