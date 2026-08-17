using UnityEditor;
using UnityEngine;

namespace PianoUI.EditorTools
{
    /// <summary>
    /// 編輯器選單：一鍵把鋼琴鍵盤放進目前的場景。
    /// 位置：上方選單列 → Tools → 鋼琴 UI
    /// </summary>
    public static class PianoKeyboardMenu
    {
        [MenuItem("Tools/鋼琴 UI/建立鍵盤到場景", false, 0)]
        public static void CreateKeyboard()
        {
#if UNITY_2023_1_OR_NEWER
            var existing = Object.FindFirstObjectByType<PianoKeyboardUI>();
#else
            var existing = Object.FindObjectOfType<PianoKeyboardUI>();
#endif
            if (existing != null)
            {
                Selection.activeGameObject = existing.gameObject;
                EditorGUIUtility.PingObject(existing.gameObject);
                Debug.Log("[鋼琴 UI] 場景裡已經有一個了，幫你選起來。直接按 Play 就會生出鍵盤。", existing);
                return;
            }

            var go = new GameObject("PianoKeyboard");
            go.AddComponent<PianoKeyboardUI>();

            Undo.RegisterCreatedObjectUndo(go, "建立鋼琴鍵盤");
            Selection.activeGameObject = go;
            EditorGUIUtility.PingObject(go);

            Debug.Log("[鋼琴 UI] 建好了。按 Play 就會自動生出 Canvas、琴鍵和角色。", go);
        }
    }
}
