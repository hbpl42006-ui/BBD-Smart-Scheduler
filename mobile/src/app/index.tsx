import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  SafeAreaView,
  Text,
  View,
} from "react-native";
import { api } from "../lib/api";
export default function HomeScreen() {
  const [message, setMessage] = useState("Backend not tested yet");
  const [loading, setLoading] = useState(false);
  const testBackend = async () => {
    setLoading(true);
    try {
      const response = await api.get("/health/");
      setMessage(`Backend connected ?\nStatus: ${response.status}`);
    } catch (error: any) {
      setMessage(
        error?.response?.data?.detail ||
          error?.message ||
          "Unable to connect to backend"
      );
    } finally {
      setLoading(false);
    }
  };
  return (
    <SafeAreaView
      style={{
        flex: 1,
        justifyContent: "center",
        padding: 24,
        backgroundColor: "#ffffff",
      }}
    >
      <View style={{ gap: 20 }}>
        <Text
          style={{
            fontSize: 30,
            fontWeight: "700",
          }}
        >
          BBD Smart Scheduler
        </Text>
        <Text style={{ fontSize: 16 }}>{message}</Text>
        <Pressable
          onPress={testBackend}
          disabled={loading}
          style={{
            backgroundColor: "#111827",
            padding: 16,
            borderRadius: 10,
            alignItems: "center",
          }}
        >
          {loading ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <Text
              style={{
                color: "#ffffff",
                fontWeight: "600",
              }}
            >
              Test Backend
            </Text>
          )}
        </Pressable>
      </View>
    </SafeAreaView>
  );
}
