package dev.mcdata.recorder

import dev.mcdata.recorder.capture.PacketNormalizer
import dev.mcdata.recorder.mixin.ServerboundInteractPacketAccessor
import org.objectweb.asm.ClassReader
import org.objectweb.asm.ClassVisitor
import org.objectweb.asm.Handle
import org.objectweb.asm.MethodVisitor
import org.objectweb.asm.Opcodes
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.io.path.extension
import kotlin.io.path.readBytes
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Guards against by-name member lookup anywhere in the compiled mod.
 *
 * Tests here run against Mojang-named Minecraft, but production runs the Loom-remapped jar against
 * intermediary names (`class_2813.method_12192`). Loom remaps compile-time member references; it
 * cannot remap a method name held in a string. A `getMethod("getSlotNum")` therefore works in
 * every unit test and silently finds nothing in production, which is how container click,
 * use-item-on, and similar packets were recorded without fields. Rejecting the lookup APIs at
 * the bytecode level catches every such call, whatever the source language or call style.
 *
 * Mixin `@Accessor`/`@Invoker` names are not affected: the mixin refmap remaps them.
 */
class ReflectionFreeTest {
    @Test
    fun `mod bytecode performs no by-name member lookup`() {
        val classes = outputRoots().flatMap { root ->
            Files.walk(root).use { paths -> paths.filter { it.extension == "class" }.toList() }
        }
        assertTrue(classes.size > 20, "expected the compiled mod classes, found ${classes.size}")

        val violations = classes.flatMap(::lookups)
        assertEquals(emptyList(), violations, "by-name reflection does not survive remapping; use typed accessors")
    }

    private fun outputRoots(): List<Path> =
        // Kotlin and Java compile to separate output directories.
        listOf(PacketNormalizer::class.java, ServerboundInteractPacketAccessor::class.java)
            .map { Paths.get(it.protectionDomain.codeSource.location.toURI()) }
            .distinct()

    private fun lookups(file: Path): List<String> {
        val found = mutableListOf<String>()
        ClassReader(file.readBytes()).accept(object : ClassVisitor(Opcodes.ASM9) {
            private var className = ""

            override fun visit(
                version: Int, access: Int, name: String, signature: String?, superName: String?, interfaces: Array<out String>?
            ) {
                className = name
            }

            override fun visitMethod(
                access: Int, name: String, descriptor: String, signature: String?, exceptions: Array<out String>?
            ): MethodVisitor = object : MethodVisitor(Opcodes.ASM9) {
                override fun visitMethodInsn(opcode: Int, owner: String, method: String, descriptor: String, isInterface: Boolean) {
                    check(owner, method)
                }

                override fun visitInvokeDynamicInsn(name: String, descriptor: String, bootstrap: Handle, vararg arguments: Any?) {
                    arguments.filterIsInstance<Handle>().forEach { check(it.owner, it.name) }
                }

                private fun check(owner: String, method: String) {
                    if (forbidden(owner, method)) found += "$className.$name calls $owner.$method"
                }
            }
        }, ClassReader.SKIP_DEBUG)
        return found
    }

    private fun forbidden(owner: String, method: String): Boolean = when (owner) {
        "java/lang/Class" -> method in CLASS_LOOKUPS
        "java/lang/reflect/Method", "java/lang/reflect/Field" -> true
        "java/lang/invoke/MethodHandles\$Lookup" -> method.startsWith("find")
        else -> owner.startsWith("kotlin/reflect/full/")
    }

    private companion object {
        val CLASS_LOOKUPS = setOf(
            "getMethod", "getMethods", "getDeclaredMethod", "getDeclaredMethods",
            "getField", "getFields", "getDeclaredField", "getDeclaredFields",
            "getRecordComponents", "forName"
        )
    }
}
