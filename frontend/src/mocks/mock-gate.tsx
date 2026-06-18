"use client";

import { useEffect } from "react";

import { initMocks } from "@/mocks/init";

export function MockGate() {
  useEffect(() => {
    void initMocks();
  }, []);
  return null;
}
